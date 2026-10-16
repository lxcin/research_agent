"""Read-only Git views supplied by the host, including linked worktrees."""
import os
import re
import subprocess
import tempfile
from pathlib import Path

from research_agent.tools.schema import ToolSchema, ToolResult


def inspect_git(workspace, *, view="status", revision="HEAD", path=None, staged=False, limit=20):
    """No arbitrary argv, shell, git-dir, external diff, pager or textconv."""
    if view not in {"status", "diff", "log", "show"}:
        raise ValueError("Unsupported Git view")
    if not isinstance(revision, str) or not re.fullmatch(r"HEAD|[0-9a-fA-F]{7,64}", revision):
        raise ValueError("Revision must be HEAD or a commit hash")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if not isinstance(staged, bool):
        raise ValueError("staged must be boolean")
    paths = []
    if path is not None:
        from research_agent.path_policy import safe_path
        resolved = safe_path(workspace, path)
        if resolved is None:
            raise ValueError("Git path must be inside the session workspace")
        paths = [Path(resolved).relative_to(Path(workspace).resolve()).as_posix()]
    base = ["git", "--no-pager", "--literal-pathspecs", "--no-replace-objects",
            "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false"]
    if view == "status":
        args = ["status", "--short", "--branch", "--untracked-files=normal"]
    elif view == "diff":
        args = ["diff", "--no-ext-diff", "--no-textconv"] + (["--cached"] if staged else [])
    elif view == "log":
        args = ["log", "--no-show-signature", f"--max-count={limit}", "--format=%H %s", revision]
    else:
        args = ["show", "--no-show-signature", "--no-ext-diff", "--no-textconv", "--format=fuller", revision]
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    # Caller environment must not redirect a worktree query to another repo.
    for key in list(env):
        if key.startswith("GIT_") and key not in {"GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT"}:
            del env[key]
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        result = subprocess.run(base + args + ["--"] + paths, cwd=workspace, env=env,
                                stdout=out, stderr=err, timeout=15)
        size = out.tell()
        out.seek(0)
        err.seek(0)
        return {"success": result.returncode == 0, "backend": "host", "view": view,
                "returncode": result.returncode, "stdout": out.read(64000).decode("utf-8", "replace"),
                "stderr": err.read(8000).decode("utf-8", "replace"), "truncated": size > 64000}


def _handle(params, llm, state, emit):
    try:
        result = inspect_git(state.workspace_dir, **params)
        return ToolResult(success=result["success"], data=result)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        return ToolResult.fail(str(exc))


git_inspect_tool = ToolSchema(
    name="git_inspect",
    description="在宿主查询当前会话工作区的 Git 状态、差异和历史。Docker 内无需安装 Git，也无需访问宿主 Git 元数据。",
    usage="查看版本信息优先使用此工具；Docker Shell 中的原始 git 不保证可用。仅查询，不提交、重置、合并或推送。",
    side_effect=False,
    handler=_handle,
    parameters={"type": "object", "additionalProperties": False, "properties": {
        "view": {"type": "string", "enum": ["status", "diff", "log", "show"]},
        "revision": {"type": "string", "description": "HEAD 或提交哈希，用于 log/show"},
        "path": {"type": "string", "description": "可选的工作区相对路径"},
        "staged": {"type": "boolean", "description": "diff 是否只查询暂存区"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    }},
)
