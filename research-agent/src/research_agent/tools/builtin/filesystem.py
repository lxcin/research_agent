"""Filesystem tools: shell_exec, file_read, file_write, file_glob, file_grep.
All operations scoped to project working directory for safety."""

import subprocess
import tempfile
import os
import shutil
import glob as glob_mod
import re
from pathlib import Path

from research_agent.tools.schema import ToolSchema, ToolResult


def _get_project_dir(state) -> str:
    """Get project working directory. Uses user-set workspace_dir if available."""
    pid = getattr(state, 'active_project', None)
    pid = getattr(pid, 'id', None) if pid else None
    
    if pid:
        from research_agent.store import get_project
        project = get_project(pid)
        # Check if project has a custom workspace dir
        if project and getattr(project, 'workspace_dir', ''):
            ws = project.workspace_dir  # type: ignore
            if ws and os.path.isdir(ws):
                return ws

    # Fallback to default data dir
    from research_agent.config import get_data_dir
    base = get_data_dir() / "projects"
    proj_dir = base / (pid or "default")
    proj_dir.mkdir(parents=True, exist_ok=True)
    return str(proj_dir)


def _safe_path(project_dir: str, user_path: str) -> str | None:
    """Resolve and validate path is within project directory."""
    resolved = os.path.normpath(os.path.join(project_dir, user_path))
    if not resolved.startswith(os.path.normpath(project_dir)):
        return None  # Path traversal attempt
    return resolved


# ── Shell Exec ──

def _handle_shell_exec(params: dict, llm, state, emit) -> ToolResult:
    command = params.get("command", "")
    timeout = int(params.get("timeout", 0)) or 300  # default 5min
    background = params.get("background", False)
    if not command.strip():
        return ToolResult.fail("Missing command parameter")

    workdir = _get_project_dir(state)
    if "sudo" in command:
        return ToolResult.fail("Dangerous command blocked")

    if background:
        # Background task: run in thread, write output to file
        import uuid, datetime
        task_id = str(uuid.uuid4())[:8]
        task_dir = os.path.join(workdir, "tasks")
        os.makedirs(task_dir, exist_ok=True)
        log_path = os.path.join(task_dir, f"{task_id}.log")
        meta_path = os.path.join(task_dir, f"{task_id}.json")

        # Write task metadata
        import json as _json
        meta = {"id": task_id, "command": command, "started": datetime.datetime.now().isoformat(),
                "status": "running", "cwd": workdir}
        with open(meta_path, "w") as f: _json.dump(meta, f)

        def _run_background():
            import subprocess as _sp
            try:
                r = _sp.run(command, shell=True, capture_output=True, text=True,
                           timeout=timeout, cwd=workdir)
                meta["status"] = "done" if r.returncode == 0 else "failed"
                meta["returncode"] = r.returncode
                output = f"STDOUT:\n{r.stdout[:4000]}\n\nSTDERR:\n{r.stderr[:2000]}"
                with open(log_path, "w") as f: f.write(output)
            except _sp.TimeoutExpired:
                meta["status"] = "timeout"
                with open(log_path, "w") as f: f.write("Task timed out")
            except Exception as e:
                meta["status"] = "error"
                with open(log_path, "w") as f: f.write(str(e))
            meta["finished"] = datetime.datetime.now().isoformat()
            with open(meta_path, "w") as f: _json.dump(meta, f)

        import threading
        t = threading.Thread(target=_run_background, daemon=True)
        t.start()
        emit("tool", {"tool": "shell_exec", "status": "background", "task_id": task_id})
        return ToolResult.ok(task_id=task_id, status="started", log_file=log_path,
                            hint=f"Check with check_tasks(task_id={task_id}) or read {log_path}")

    emit("tool", {"tool": "shell_exec", "status": "start", "command": command[:80]})
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=min(timeout, 300), cwd=workdir,
        )
        stdout = result.stdout[:4000]
        stderr = result.stderr[:2000]
        success = result.returncode == 0

        emit("tool", {"tool": "shell_exec", "status": "done" if success else "error"})
        return ToolResult.ok(
            success=success,
            stdout=stdout,
            stderr=stderr,
            returncode=result.returncode,
            cwd=workdir,
            hint="stderr/error above shows what went wrong, use file_edit to fix" if not success else "",
        )
    except subprocess.TimeoutExpired:
        emit("tool", {"tool": "shell_exec", "status": "error", "error": "timeout"})
        return ToolResult.fail("Command timed out (30s limit)")
    except Exception as e:
        return ToolResult.fail(str(e))


# ── File Tools ──

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

    # Limit binary files
    if os.path.getsize(full_path) > 1_000_000:
        return ToolResult.fail("File too large (>1MB)")

    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()[:8000]
        emit("tool", {"tool": "file_read", "status": "done", "path": path})
        return ToolResult.ok(
            path=path,
            content=content,
            size=len(content),
            lines=content.count("\n") + 1,
        )
    except Exception as e:
        return ToolResult.fail(str(e))


def _handle_file_write(params: dict, llm, state, emit) -> ToolResult:
    path = params.get("path", "")
    content = params.get("content", "")
    if not path:
        return ToolResult.fail("Missing path parameter")

    proj_dir = _get_project_dir(state)
    full_path = _safe_path(proj_dir, path)
    if not full_path:
        return ToolResult.fail(f"Path outside project directory: {path}")

    try:
        os.makedirs(os.path.dirname(full_path) or proj_dir, exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        emit("tool", {"tool": "file_write", "status": "done", "path": path})
        return ToolResult.ok(path=path, size=len(content))
    except Exception as e:
        return ToolResult.fail(str(e))


def _handle_file_glob(params: dict, llm, state, emit) -> ToolResult:
    pattern = params.get("pattern", "*")
    proj_dir = _get_project_dir(state)
    try:
        matches = glob_mod.glob(pattern, root_dir=proj_dir, recursive=True)
        matches = [m for m in matches if not m.startswith(".git")]
        emit("tool", {"tool": "file_glob", "status": "done", "count": len(matches)})
        return ToolResult.ok(
            pattern=pattern,
            matches=matches[:50],
            count=len(matches),
        )
    except Exception as e:
        return ToolResult.fail(str(e))


def _handle_file_grep(params: dict, llm, state, emit) -> ToolResult:
    pattern = params.get("pattern", "")
    include = params.get("include", "*")
    if not pattern:
        return ToolResult.fail("Missing pattern parameter")

    proj_dir = _get_project_dir(state)
    try:
        regex = re.compile(pattern)
        matches = []
        for fpath in glob_mod.glob(f"**/{include}", root_dir=proj_dir, recursive=True):
            full = os.path.join(proj_dir, fpath)
            if not os.path.isfile(full):
                continue
            if os.path.getsize(full) > 500_000:
                continue
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, 1):
                        if regex.search(line):
                            matches.append({"file": fpath, "line": lineno, "content": line.strip()[:200]})
                            if len(matches) >= 30:
                                break
            except Exception:
                continue
            if len(matches) >= 30:
                break

        emit("tool", {"tool": "file_grep", "status": "done", "count": len(matches)})
        return ToolResult.ok(pattern=pattern, matches=matches, count=len(matches))
    except re.error as e:
        return ToolResult.fail(f"Invalid regex: {e}")
    except Exception as e:
        return ToolResult.fail(str(e))


# ── Tool Definitions ──

shell_exec_tool = ToolSchema(
    name="shell_exec",
    description="执行 Shell 命令。background=true 时在后台运行，用 check_tasks 查状态。超时默认 5min。",
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
    """Check status of background tasks."""
    task_id = params.get("task_id", "")
    workdir = _get_project_dir(state)
    task_dir = os.path.join(workdir, "tasks")
    if not os.path.isdir(task_dir):
        return ToolResult.ok(tasks=[], count=0)

    import json as _json
    tasks = []
    for f in sorted(os.listdir(task_dir)):
        if f.endswith(".json"):
            with open(os.path.join(task_dir, f)) as fh:
                meta = _json.load(fh)
            log_file = meta.get("id", f.replace(".json", "")) + ".log"
            log_path = os.path.join(task_dir, log_file)
            if os.path.exists(log_path):
                with open(log_path) as fh:
                    meta["output"] = fh.read()[:3000]
            if task_id and meta.get("id") != task_id:
                continue
            tasks.append(meta)

    running = [t for t in tasks if t.get("status") == "running"]
    done = [t for t in tasks if t.get("status") != "running"]

    emit("tool", {"tool": "check_tasks", "status": "done", "total": len(tasks)})
    return ToolResult.ok(tasks=tasks, running=len(running), done=len(done), total=len(tasks),
                        hint="Tasks running" if running else "All tasks completed")


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
    description="读取项目目录下的文件内容。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对于项目目录的文件路径"}
        },
        "required": ["path"],
    },
    handler=_handle_file_read,
    category="builtin",
)

file_write_tool = ToolSchema(
    name="file_write",
    description="写入内容到文件。用于创建脚本、保存结果。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对于项目目录的文件路径"},
            "content": {"type": "string", "description": "要写入的文件内容"},
        },
        "required": ["path", "content"],
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
    description="在项目文件中搜索正则匹配。",
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
        return ToolResult.fail("Missing path")
    if not old_str:
        return ToolResult.fail("Missing old_string")

    proj_dir = _get_project_dir(state)
    full_path = _safe_path(proj_dir, path)
    if not full_path:
        return ToolResult.fail(f"Path outside project: {path}")
    if not os.path.isfile(full_path):
        return ToolResult.fail(f"File not found: {path}")

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()

        count = content.count(old_str)
        if count == 0:
            return ToolResult.fail("old_string not found in file")
        if count > 1:
            return ToolResult.fail(f"old_string found {count} times (must be unique). Use more context.")

        new_content = content.replace(old_str, new_str, 1)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        emit("tool", {"tool": "file_edit", "status": "done", "path": path})
        return ToolResult.ok(path=path, replaced=True, old_length=len(old_str), new_length=len(new_str))
    except Exception as e:
        return ToolResult.fail(str(e))


file_edit_tool = ToolSchema(
    name="file_edit",
    description="精确替换文件内容。old_string 必须唯一，否则报错。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "old_string": {"type": "string", "description": "要替换的原文本（必须文件中唯一）"},
            "new_string": {"type": "string", "description": "替换后的新文本"},
        },
        "required": ["path", "old_string", "new_string"],
    },
    handler=_handle_file_edit,
    category="builtin",
)