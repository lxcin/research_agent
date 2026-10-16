"""Filesystem tools: shell_exec, file_read, file_write, file_glob, file_grep.
All operations scoped to project working directory for safety."""

import os
import glob as glob_mod
import json
import re
import shutil
import subprocess
import hashlib
import difflib
import tempfile
from pathlib import Path

from research_agent.tools.schema import ToolSchema, ToolResult
from research_agent import file_views as fv
from research_agent.sandbox import (
    run_command, resolve_backend, get_sandbox_config, get_session,
)


def _get_project_dir(state) -> str:
    """Get project working directory from state.workspace_dir."""
    ws = getattr(state, 'workspace_dir', '')
    if ws and os.path.isdir(ws):
        return ws
    from research_agent.config import get_data_dir
    ws = str(get_data_dir() / "workspaces" / "default")
    os.makedirs(ws, exist_ok=True)
    state.workspace_dir = ws
    return ws


def _safe_path(project_dir: str, user_path: str) -> str | None:
    """Resolve and validate path is within project/workspace directory."""
    from research_agent.path_policy import safe_path
    return safe_path(project_dir, user_path)


# ── Shell Exec ──

def _maybe_checkpoint(state, workdir: str) -> str:
    """Resolve the workspace checkpoint ref reported on this shell_exec.

    Granularity (config `shell.checkpoint`):
      - turn (default): the run-level checkpoint created by the host; report it.
      - command       : legacy value, now treated as turn.
      - off           : none.
    Best-effort, never raises. See research_agent.checkpoint.
    """
    try:
        from research_agent import checkpoint as _ckpt
        mode = _ckpt.get_checkpoint_mode()
        if mode == "off" or not _ckpt.is_repo(workdir):
            return ""
        # turn: created by the host at run start; just report it
        return getattr(state, "_last_checkpoint", "") or ""
    except Exception:
        return ""


def _handle_shell_exec(params: dict, llm, state, emit) -> ToolResult:
    from research_agent.execution import Cancelled, RecoveryRequired
    command = params.get("command", "")
    timeout = int(params.get("timeout", 0)) or 300  # default 5min
    background = params.get("background", False)
    if not command.strip():
        return ToolResult.fail("Missing command parameter")

    workdir = _get_project_dir(state)
    if "sudo" in command:
        return ToolResult.fail("Dangerous command blocked")

    cp_ref = _maybe_checkpoint(state, workdir)

    if background:
        from contextlib import ExitStack
        from research_agent.tools import get_registry
        from research_agent.process_control import run_local, run_docker
        registry = get_registry()
        cfg = get_sandbox_config()
        backend, warning = resolve_backend(cfg)
        if backend == "docker" and warning:
            return ToolResult.fail(warning)
        # Capture request identities and token now, never read a later turn's
        # mutable state from the worker thread.
        chat = getattr(state, "active_chat_id", "") or "default"
        execution = getattr(state, "_execution_id", "")
        parent = getattr(state, "_cancellation", None)
        leases = ExitStack()
        try:
            request_lease = getattr(state, "_request_lease", None)
            if request_lease is not None:
                leases.callback(request_lease.retain())
            ctx = registry.lifecycle.contexts.get("shell")
            if ctx is not None:
                leases.enter_context(ctx.scope.acquire())
            leases.enter_context(registry.session_scope(chat).acquire())
            def invoke(token):
                if backend == "local":
                    return run_local(command, workdir, timeout, token)
                return run_docker(command, workdir, timeout, token, cfg=cfg)
            descriptor = registry.background_tasks.start(
                chat=chat, execution=execution, workspace=workdir, command=command,
                backend=backend, invoke=invoke, leases=leases, parent=parent, checkpoint=cp_ref,
                request_lease=request_lease)
        except BaseException:
            leases.close()
            raise
        emit("tool", {"tool": "shell_exec", "status": "background", **descriptor})
        return ToolResult.ok(**descriptor,
                            hint="Query or cancel this task through the background task controls")

    emit("tool", {"tool": "shell_exec", "status": "start", "command": command[:80]})
    limit = min(timeout, 300)
    # Foreground commands reuse one sandbox session per run (session-scoped, not
    # per-command) so a batch of shell_exec calls pays the container-start cost
    # once. Background tasks keep the one-shot path (they may outlive the run).
    try:
        session = get_session(state, workdir)
        token = getattr(state, "_cancellation", None)
        if token is not None and session.backend == "local":
            from research_agent.process_control import run_local
            res = run_local(command, workdir, limit, token)
        elif token is not None:
            from research_agent.process_control import run_docker
            res = run_docker(command, workdir, limit, token, session=session)
        else:
            res = session.exec(command, limit)
    except Exception as e:
        res = {"success": False, "backend": "local", "returncode": -1,
               "stdout": "", "stderr": str(e), "error": str(e)}
    backend = res.get("backend", "local")
    if res.get("error") == "timeout":
        emit("tool", {"tool": "shell_exec", "status": "error", "error": "timeout"})
        return ToolResult.fail(f"Command timed out ({limit}s limit)")
    stdout = (res.get("stdout") or "")[:4000]
    stderr = (res.get("stderr") or "")[:2000]
    success = bool(res.get("success"))
    if res.get("warning"):
        stderr = f"[sandbox] {res['warning']}\n{stderr}"

    emit("tool", {"tool": "shell_exec", "status": "done" if success else "error",
                  "backend": backend})
    return ToolResult.ok(
        success=success,
        stdout=stdout,
        stderr=stderr,
        returncode=res.get("returncode"),
        cwd=workdir,
        backend=backend,
        checkpoint=cp_ref,
        # Neutral, accurate failure hint: don't presume the cause is a file that
        # needs editing (env/command/path errors are common). The model has
        # returncode + stderr to reason from.
        hint=(f"命令失败（exit={res.get('returncode')}）；原因见 stderr。"
              "当前工作目录已是项目目录，无需 cd。" if not success else ""),
    )


# ── File Tools ──

def _read_history(data):
    keys = ("path", "version", "range", "range_hash", "read_coverage", "offset", "next_offset", "total_characters", "start_line", "end_line", "sha256",
            "has_more", "error", "error_code", "outcome")
    return {**{key: data[key] for key in keys if key in data},
            "note": "读取正文已折叠；需要源码时调用 file_read。版本变化时旧读取过期。"}


def _probe_files(state):
    changes = fv.probe(state)
    if not changes:
        return ""
    return ("[文件版本变化：相关旧读取已过期；需要源码时自行调用 file_read，不自动恢复或注入源码]\n"
            + json.dumps({"files": changes[:30], "total": len(changes)}, ensure_ascii=False))


def _read_delivery(state, raw, visible):
    if "content" not in raw or "version" not in raw:
        return
    full_path = _safe_path(_get_project_dir(state), raw["path"])
    if not full_path:
        return
    try:
        current = list(fv._signature(os.stat(full_path)))
    except OSError:
        current = None
    record = fv.views(state).get(fv.identity(state, full_path), {})
    if current != raw.get("file_stamp"):
        if record:
            fv.save_view(state, full_path, dict(record, stale=True, disk_version=None, disk_stamp=current))
        reference = visible.get("_tool_result_id")
        visible.clear()
        visible.update(_read_history(raw), stale=True, error_code="FILE_CHANGED", disk_version=None,
                       instruction="读取记录已过期；调用 file_read 获取当前内容，不恢复旧源码。")
        if reference:
            visible["_tool_result_id"] = reference
        return
    if not visible.get("_truncated"):
        return
    if record.get("version") == raw["version"]:
        # A preview is not a complete delivered read.
        fv.save_view(state, full_path, dict(record, coverage=[], read_coverage=[]))
    visible["instruction"] = "本次正文超出工具结果预算，不计为完整已读范围；请调用 file_read 缩小行范围或 limit，不要凭预览编辑。"


def _edit_call(params):
    return {key: value for key, value in params.items() if key == "path"}


def _edit_failure(path, error, code="EDIT_FAILED"):
    hints = {
        "FILE_CHANGED": "文件在读取后或写入前发生变化，本次未写入。旧行号可能偏移：先用 file_grep 定位目标，再用 file_read 读取相关范围，取得新 version 后重新规划。不要原样重试，也不要只替换 expected_version。",
        "NO_MATCH": "当前版本中找不到 old_string，本次未写入。检索目标函数或附近文本，读取相关范围后重新构造 old_string；无需默认重读整个文件。",
        "MULTI_MATCH": "old_string 存在多处匹配，本次未写入。检索匹配位置并读取目标附近范围，增加前后文使 old_string 唯一；不要任意选择一个匹配。",
    }
    data = {"path": path, "error": error, "error_code": code, "status": "failed",
            "completed": False, "verification": "not_attempted"}
    if code in hints:
        data["instruction"] = hints[code]
    return ToolResult(False, data)


def _read_for_edit(full_path):
    with open(full_path, "rb") as stream:
        content = stream.read(fv.MAX_FILE_BYTES + 1)
    if len(content) > fv.MAX_FILE_BYTES:
        raise fv.FileViewError("FILE_TOO_LARGE", "File exceeds edit safety limit")
    if b"\0" in content:
        raise fv.FileViewError("BINARY_FILE", "Binary content cannot be edited as text")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise fv.FileViewError("UNSUPPORTED_ENCODING", "Only strict UTF-8 / UTF-8 BOM text is supported") from exc
    return content


def _edit_report(path, before, after, operation):
    def lines(raw):
        parts = raw.decode("utf-8").split("\n")
        return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
    old, new = lines(before or b""), lines(after)
    changes = [{"kind": tag, "old_start_line": a + 1, "old_line_count": b - a,
                "new_start_line": c + 1, "new_line_count": d - c}
               for tag, a, b, c, d in difflib.SequenceMatcher(None, old, new).get_opcodes() if tag != "equal"]
    diff = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        for line in difflib.unified_diff(old, new, fromfile=path + ":before", tofile=path + ":after", n=3))
    return {"path": path, "operation": operation, "status": "success", "completed": True, "verification": "read_back_match",
            "changed": before != after, "size": len(after), "changes": changes,
            "diff": diff,
            "before_sha256": hashlib.sha256(before).hexdigest() if before is not None else None,
            "after_sha256": hashlib.sha256(after).hexdigest(),
            "validation": "not_run_by_edit_tool", "note": "文件编辑已读回核对；语法检查与测试结果须另行判断。"}


def _edit_result_context(data):
    result = {key: value for key, value in data.items() if key not in ("changes", "diff")}
    if "diff" in data:
        result.update(diff=data["diff"][:8000], diff_truncated=len(data["diff"]) > 8000)
    changes = data.get("changes", [])
    if "changes" in data:
        result.update(changes=changes[:20], change_count=len(changes), changes_truncated=len(changes) > 20)
    return result


def _write_verified(path, full_path, before, after, operation, state, known_span=None):
    expected = hashlib.sha256(before).hexdigest() if before is not None else "absent"
    descriptor, temporary = tempfile.mkstemp(dir=Path(full_path).parent, prefix=".agent-edit-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(after)
            stream.flush()
            os.fsync(stream.fileno())
        if before is not None:
            shutil.copymode(full_path, temporary)
        current = fv.fingerprint(full_path) if Path(full_path).exists() else "absent"
        if current != expected:
            return _edit_failure(path, "File changed before write; read again", "FILE_CHANGED")
        if before is None:
            # Exclusive creation: unlike replace, link cannot overwrite a racing creator.
            try:
                os.link(temporary, full_path)
            except FileExistsError:
                return _edit_failure(path, "File appeared before creation; read again", "FILE_CHANGED")
        else:
            os.replace(temporary, full_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    actual = Path(full_path).read_bytes()
    if actual != after:
        return ToolResult(False, {"path": path, "operation": operation, "completed": False,
            "status": "partial", "verification": "read_back_mismatch", "error": "编辑后内容与目标不一致；请重新读取当前文件。"})
    report = _edit_report(path, before, after, operation)
    report.update(hash_before=report["before_sha256"], hash_after=report["after_sha256"], version=report["after_sha256"])
    # Only the exact replacement is established on the new version. Do not
    # pretend unchanged regions were re-read or try to guess shifted ranges.
    text = after.decode("utf-8")
    coverage = [[0, len(text)]] if operation in ("create", "replace") else []
    if operation == "edit":
        coverage = [list(known_span)] if known_span is not None else []
    fv.views(state)
    if before != after:
        stamp = list(fv._signature(os.stat(full_path)))
        fv.save_view(state, full_path, dict(path=path, version=report["version"], disk_version=report["version"],
                                          coverage=coverage, read_coverage=[], stale=False,
                                          file_stamp=stamp, disk_stamp=stamp))
    from research_agent.context_budget import store_artifact
    report["diff_ref"] = store_artifact({"path": path, "diff": report["diff"],
        "hash_before": report["hash_before"], "hash_after": report["hash_after"]}, state)
    if before != after:
        report["note"] += " 此前该文件的读取属于旧版本；新版本仅确认本次写出片段，其他范围按需重读。"
    return ToolResult.ok(**report)


def _guard_version(params, state, full_path, before, *, whole=False):
    path = params["path"]
    expected = params.get("expected_version")
    current = hashlib.sha256(before).hexdigest() if before is not None else "absent"
    if expected is None:
        return _edit_failure(path, "Provide expected_version from file_read, or absent for creation", "VERSION_REQUIRED")
    if expected != current:
        record = fv.views(state).get(fv.identity(state, full_path))
        if record:
            fv.save_view(state, full_path, dict(record, stale=True, disk_version=current))
        result = _edit_failure(path, "File version changed; call file_read before replanning", "FILE_CHANGED")
        result.data.update(expected_version=expected, version=current)
        return result
    if before is None:
        return None
    record = fv.views(state).get(fv.identity(state, full_path), {})
    if record.get("version") != current or record.get("stale"):
        return _edit_failure(path, "No valid read of this version; call file_read", "NOT_READ")
    if whole and not fv.covered(record, 0, len(before.decode("utf-8"))):
        return _edit_failure(path, "Whole-file replacement requires complete read coverage", "NOT_READ")
    return None


def _handle_file_read(params: dict, llm, state, emit) -> ToolResult:
    path = params.get("path", "")
    if not path:
        return ToolResult.fail("Missing path parameter")

    proj_dir = _get_project_dir(state)
    full_path = _safe_path(proj_dir, path)
    if not full_path:
        return ToolResult.fail(f"Path outside project directory: {path}")

    if not os.path.isfile(full_path):
        return ToolResult.fail(f"File not found: {path}")

    try:
        result = fv.scan(full_path, start_line=int(params.get("start_line", 1)),
            end_line=int(params["end_line"]) if "end_line" in params else None,
            column=int(params.get("start_column", 1)), limit=int(params.get("limit", 8000)))
        result["path"] = path
        fv.record_read(state, full_path, path, result)
        if emit:
            emit("tool", {"tool": "file_read", "status": "done", "path": path})
        return ToolResult.ok(**result)
    except fv.FileViewError as e:
        data = {"error": str(e), "error_code": e.code, "path": path}
        if e.code in ("BINARY_FILE", "UNSUPPORTED_ENCODING"):
            data.update(version=fv.fingerprint(full_path), bytes=os.path.getsize(full_path))
        return ToolResult(False, data)
    except Exception as e:
        return ToolResult.fail(str(e))


def _handle_file_write(params: dict, llm, state, emit) -> ToolResult:
    path = params.get("path", "")
    content = params.get("content", "")
    if not path:
        return _edit_failure(path, "Missing path parameter")

    proj_dir = _get_project_dir(state)
    full_path = _safe_path(proj_dir, path)
    if not full_path:
        return _edit_failure(path, f"Path outside project directory: {path}")

    try:
        if Path(full_path).exists() and Path(full_path).stat().st_size > fv.MAX_FILE_BYTES:
            return _edit_failure(path, "File exceeds edit safety limit", "FILE_TOO_LARGE")
        before = _read_for_edit(full_path) if Path(full_path).exists() else None
        after = content.encode("utf-8")
        if len(after) > fv.MAX_FILE_BYTES or b"\0" in after or (before is not None and b"\0" in before):
            return _edit_failure(path, "Unsupported binary content or file too large", "UNSUPPORTED_CONTENT")
        rejected = _guard_version(params, state, full_path, before, whole=True)
        if rejected:
            return rejected
        os.makedirs(os.path.dirname(full_path) or proj_dir, exist_ok=True)
        operation = "create" if before is None else "replace"
        result = _write_verified(path, full_path, before, after, operation, state)
        if result.success and params.get("intent"):
            result.data["declared_intent"] = params["intent"]
        if result.success:
            if emit:
                emit("file_change", {"tool_id": getattr(state, '_current_tool_id', 'unknown'), "path": path,
                                 "action": operation, "completed": True, "changes": result.data["changes"][:20]})
        return result
    except fv.FileViewError as e:
        return _edit_failure(path, str(e), e.code)
    except Exception as e:
        return ToolResult(False, {"error": str(e), "path": path, "completed": False,
                                  "status": "partial", "verification": "unconfirmed", "instruction": "请重新读取文件核实编辑状态。"})


def _handle_file_glob(params: dict, llm, state, emit) -> ToolResult:
    pattern = params.get("pattern", "*")
    proj_dir = _get_project_dir(state)
    try:
        from research_agent.path_policy import safe_glob
        matches = list(safe_glob(proj_dir, pattern))
        emit("tool", {"tool": "file_glob", "status": "done", "count": len(matches)})
        return ToolResult.ok(
            pattern=pattern,
            matches=matches[:50],
            count=len(matches),
        )
    except Exception as e:
        return ToolResult.fail(str(e))


def _grep_with_rg(proj_dir: str, pattern: str, include: str,
                  max_matches: int = 30, max_filesize: str = "64M"):
    """Ripgrep-backed search. Returns matches, or None to fall back to Python.

    None is returned when rg is unavailable, errors (e.g. invalid regex → exit 2),
    or times out — the caller then uses the pure-Python scan (which also surfaces
    invalid-regex errors properly).
    """
    if not shutil.which("rg"):
        return None
    from research_agent.path_policy import safe_glob
    candidates = [name for name in safe_glob(proj_dir, "**/*")
                  if os.path.isfile(_safe_path(proj_dir, name) or "")]
    if not candidates:
        return []
    argv = [
        "rg", "--json", "--no-ignore", "--max-filesize", max_filesize,
        "--glob", include or "*", "--glob", "!.git/**",
        "--no-follow", "-e", pattern, "--", *candidates,
    ]
    try:
        r = subprocess.run(argv, cwd=proj_dir, capture_output=True, text=True,
                           errors="replace", timeout=30)
    except Exception:
        return None
    if r.returncode == 2:          # rg error (bad regex / path) → let Python handle it
        return None
    out: list[dict] = []
    for line in (r.stdout or "").splitlines():
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("type") != "match":
            continue
        d = obj.get("data") or {}
        path = ((d.get("path") or {}).get("text") or "").replace("\\", "/")
        if path.startswith("./"):
            path = path[2:]
        out.append({
            "file": path,
            "line": d.get("line_number") or 0,
            "content": ((d.get("lines") or {}).get("text") or "").rstrip("\n").strip()[:200],
        })
        if len(out) >= max_matches:
            break
    return out


def _grep_python(proj_dir: str, pattern: str, include: str,
                 max_matches: int = 30, max_size: int = fv.MAX_FILE_BYTES) -> list[dict]:
    """Pure-Python fallback: glob + line-by-line regex. Raises re.error on bad regex."""
    regex = re.compile(pattern)
    matches: list[dict] = []
    from research_agent.path_policy import safe_glob
    for fpath in safe_glob(proj_dir, f"**/{include}"):
        full = _safe_path(proj_dir, fpath)
        if full is None:
            continue
        if not os.path.isfile(full) or os.path.getsize(full) > max_size:
            continue
        try:
            with open(full, "r", encoding="utf-8", errors="strict", newline="") as f:
                lineno, column = 1, 1
                while line := f.readline(16000):
                    if regex.search(line):
                        matches.append({"file": fpath.replace("\\", "/"),
                                        "line": lineno, "column": column, "content": line.strip()[:200],
                                        "segment_truncated": not line.endswith("\n") and len(line) == 16000})
                        if len(matches) >= max_matches:
                            break
                    if line.endswith("\n"):
                        lineno, column = lineno + 1, 1
                    else:
                        column += len(line)
        except Exception:
            continue
        if len(matches) >= max_matches:
            break
    return matches


def _handle_file_grep(params: dict, llm, state, emit) -> ToolResult:
    pattern = params.get("pattern", "")
    include = params.get("include", "*")
    if not pattern:
        return ToolResult.fail("Missing pattern parameter")

    proj_dir = _get_project_dir(state)
    backend = "rg"
    try:
        from research_agent.path_policy import glob_parts
        glob_parts(include)
        re.compile(pattern)  # Invalid patterns must fail even in an empty workspace.
        # rg follows neither symlinks nor junctions by default; fallback uses safe traversal.
        matches = _grep_with_rg(proj_dir, pattern, include)   # fast path
        if matches is None:
            backend = "python"
            matches = _grep_python(proj_dir, pattern, include)  # fallback
    except re.error as e:
        return ToolResult.fail(f"Invalid regex: {e}")
    except Exception as e:
        return ToolResult.fail(str(e))

    emit("tool", {"tool": "file_grep", "status": "done",
                  "count": len(matches), "backend": backend})
    return ToolResult.ok(pattern=pattern, matches=matches, count=len(matches))


# ── Tool Definitions ──

shell_exec_tool = ToolSchema(
    usage="连续相关命令可合并执行以减少交互；前台命令复用沙箱会话，快照粒度由配置决定。长任务可使用 background 并查询状态。",
    name="shell_exec",
    description=(
        "执行 Shell 命令。background=true 时在后台运行，用 check_tasks 查状态。超时默认 5min。"
        "要跑多个脚本/命令时必须合并成一条命令（如 for f in *.py; do python \"$f\"; done，"
        "或写一个 runner/Makefile 一次跑完），减少不必要的工具往返。"
        "长任务（训练/服务）用 background=true，也不要靠拆成多条短命令绕过。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "timeout": {"type": "integer", "description": "超时秒数（默认300）"},
            "background": {"type": "boolean", "description": "是否后台运行，长任务（训练等）设为 true"},
        },
        "required": ["command"],
    },
    handler=_handle_shell_exec,
    category="builtin",
)


def _handle_check_tasks(params: dict, llm, state, emit) -> ToolResult:
    from research_agent.tools import get_registry
    tasks = get_registry().background_tasks.list(
        getattr(state, "active_chat_id", "") or "default", params.get("task_id", ""))
    running = sum(task["status"] in ("running", "cancelling") for task in tasks)
    emit("tool", {"tool": "check_tasks", "status": "done", "total": len(tasks)})
    return ToolResult.ok(tasks=tasks, running=running, done=len(tasks)-running, total=len(tasks))


def _handle_cancel_task(params, llm, state, emit):
    from research_agent.tools import get_registry
    ids = get_registry().background_tasks.cancel(
        chat=getattr(state, "active_chat_id", "") or "default", task_id=params["task_id"])
    if not ids:
        return ToolResult.fail("No active task with this identity in the current session")
    return ToolResult.ok(task_id=ids[0], status="cancellation_requested")


cancel_task_tool = ToolSchema(
    name="cancel_task", description="请求取消当前会话的后台任务；取消后等待任务和容器清理完成。",
    parameters={"type": "object", "additionalProperties": False,
                "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
    handler=_handle_cancel_task, category="builtin",
)


check_tasks_tool = ToolSchema(
    name="check_tasks",
    description="查询后台任务状态。不传 task_id 则列出所有任务。用于检查训练、实验等长任务的进度。",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "可选，指定任务 ID"},
        },
        "required": [],
    },
    handler=_handle_check_tasks,
    category="builtin",
)

file_read_tool = ToolSchema(
    name="file_read",
    description="按行读取 UTF-8 项目文件，返回文件版本、实际片段与本版本已读范围；超长行按字符列续读。",
    usage="先搜索定位再按需读取；版本未变可复用已读片段，无须每次重读。编辑携带 version 作为 expected_version；版本变化或目标未读时重新读取。按 next_cursor.line/column 续读。哈希按原始字节计算；最多 64 MiB，每页最多 16000 字符。",
    history_result=_read_history, version="3", defer_context=True, context_probe=_probe_files,
    context_delivery=_read_delivery,
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对于项目目录的文件路径"},
            "start_line": {"type": "integer", "minimum": 1, "description": "起始行，含，默认 1"},
            "end_line": {"type": "integer", "minimum": 1, "description": "结束行，含；省略则读到页上限"},
            "start_column": {"type": "integer", "minimum": 1, "description": "起始行的 Unicode 字符列，默认 1，用于超长行续读"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 16000, "description": "读取字符数，默认 8000"}
        },
        "required": ["path"],
    },
    handler=_handle_file_read,
    category="builtin",
)

file_write_tool = ToolSchema(
    usage="已有文件必须完整读取且携带 expected_version；新建文件携带 absent，禁止覆盖意外出现的文件。优先精确编辑；FILE_CHANGED 后重新读取，不自动重试。查看 completed、diff 和 verification；编辑完成不代表测试通过。",
    context_call=_edit_call, context_result=_edit_result_context, version="3", defer_context=True,
    name="file_write",
    description="写入内容到文件。用于创建脚本、保存结果。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对于项目目录的文件路径"},
            "content": {"type": "string", "description": "要写入的文件内容"},
            "expected_version": {"type": "string", "pattern": "^(absent|[a-f0-9]{64})$", "description": "已有文件读取版本；新建必须为 absent"},
            "intent": {"type": "string", "description": "声明修改原因与目的；实际影响以 diff 和验证为准"},
        },
        "required": ["path", "content", "expected_version"],
    },
    handler=_handle_file_write,
    category="builtin",
)

file_glob_tool = ToolSchema(
    name="file_glob",
    description="在项目目录中查找文件。",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "文件匹配模式，如 *.py 或 **/*.csv"}
        },
        "required": ["pattern"],
    },
    handler=_handle_file_glob,
    category="builtin",
)

file_grep_tool = ToolSchema(
    name="file_grep",
    description="在项目文件中搜索正则匹配（优先用 ripgrep，缺失时回退纯 Python 扫描）。",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式搜索模式"},
            "include": {"type": "string", "description": "文件过滤模式，如 *.py"},
        },
        "required": ["pattern"],
    },
    handler=_handle_file_grep,
    category="builtin",
)


def _handle_file_edit(params: dict, llm, state, emit) -> ToolResult:
    """Precise string replacement in a file. Fails if old_string not found or found multiple times."""
    path = params.get("path", "")
    old_str = params.get("old_string", "")
    new_str = params.get("new_string", "")
    if not path:
        return _edit_failure(path, "Missing path")
    if not old_str:
        return _edit_failure(path, "Missing old_string")

    proj_dir = _get_project_dir(state)
    full_path = _safe_path(proj_dir, path)
    if not full_path:
        return _edit_failure(path, f"Path outside project: {path}")
    if not os.path.isfile(full_path):
        return _edit_failure(path, f"File not found: {path}")

    try:
        if Path(full_path).stat().st_size > fv.MAX_FILE_BYTES:
            return _edit_failure(path, "File exceeds edit safety limit", "FILE_TOO_LARGE")
        before = _read_for_edit(full_path)
        rejected = _guard_version(params, state, full_path, before)
        if rejected:
            return rejected
        if b"\0" in before or "\0" in new_str:
            return _edit_failure(path, "Binary content cannot be edited as text", "BINARY_FILE")
        content = before.decode("utf-8")
        if b"\r\n" in before and b"\n" not in before.replace(b"\r\n", b""):
            old_str = old_str.replace("\r\n", "\n").replace("\n", "\r\n")
            new_str = new_str.replace("\r\n", "\n").replace("\n", "\r\n")

        count = content.count(old_str)
        # str.count ignores overlapping matches ("aaa" occurs twice in "aaaa").
        if count == 1 and content.find(old_str, content.find(old_str) + 1) >= 0:
            count = 2
        if count == 0:
            result = _edit_failure(path, "old_string not found in file", "NO_MATCH")
            result.data.update(match_count=0, version=params["expected_version"])
            return result
        if count > 1:
            result = _edit_failure(path, f"old_string found {count} times; unique match required", "MULTI_MATCH")
            result.data.update(match_count=count, match_count_is_lower_bound=True, version=params["expected_version"])
            return result

        start = content.index(old_str)
        record = fv.views(state)[fv.identity(state, full_path)]
        if not fv.covered(record, start, start + len(old_str)):
            return _edit_failure(path, "Target is outside read coverage; read that range", "NOT_READ")

        new_content = content.replace(old_str, new_str, 1)
        after = new_content.encode("utf-8")
        if len(after) > fv.MAX_FILE_BYTES:
            return _edit_failure(path, "Result exceeds edit safety limit", "FILE_TOO_LARGE")
        result = _write_verified(path, full_path, before, after, "edit", state, known_span=(start, start + len(new_str)))
        if result.success:
            if params.get("intent"):
                result.data["declared_intent"] = params["intent"]
            result.data.update(replaced=True, old_length=len(old_str), new_length=len(new_str))
            if emit:
                emit("file_change", {"tool_id": getattr(state, '_current_tool_id', 'unknown'), "path": path,
                                 "action": "edit", "completed": True, "changes": result.data["changes"][:20]})
        return result
    except fv.FileViewError as e:
        return _edit_failure(path, str(e), e.code)
    except Exception as e:
        return ToolResult(False, {"error": str(e), "path": path, "completed": False, "status": "partial", "verification": "unconfirmed"})


file_edit_tool = ToolSchema(
    name="file_edit",
    usage="携带读取所得 expected_version；old_string 必须唯一且完全位于该版本已读范围内。版本未变不必反复读取；FILE_CHANGED 或 NOT_READ 时按需重读后重新规划，不自动重试。查看 completed、diff 和 verification，测试状态独立。",
    context_call=_edit_call, context_result=_edit_result_context, version="3", defer_context=True,
    description="精确替换文件内容。old_string 必须唯一，否则报错。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "old_string": {"type": "string", "description": "要替换的原文本（必须文件中唯一）"},
            "new_string": {"type": "string", "description": "替换后的新文本"},
            "expected_version": {"type": "string", "pattern": "^[a-f0-9]{64}$", "description": "读取返回的文件版本"},
            "intent": {"type": "string", "description": "声明修改原因与目的；实际影响以 diff 和验证为准"},
        },
        "required": ["path", "old_string", "new_string", "expected_version"],
    },
    handler=_handle_file_edit,
    category="builtin",
)

# Input constraints belong to their declaring capability, not the runtime.
for _tool in (file_read_tool, file_write_tool, file_edit_tool):
    _tool.parameters["additionalProperties"] = False
    _tool.parameters["properties"]["path"].update(minLength=1, pattern=r"\S")
shell_exec_tool.parameters["properties"]["command"].update(minLength=1, pattern=r"\S")
shell_exec_tool.parameters["properties"]["timeout"].update(minimum=1, maximum=86400)
