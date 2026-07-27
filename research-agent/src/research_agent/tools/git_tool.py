"""Git integration tools — experiment version management and checkpoints."""
import os
import subprocess
from pathlib import Path

from research_agent.tools.schema import ToolSchema, ToolResult


def _get_project_git_dir(state) -> str | None:
    from research_agent.tools.builtin.filesystem import _get_project_dir
    ws = _get_project_dir(state)
    if ws:
        return ws
    return None


def _run_git(args: list[str], cwd: str, timeout: int = 15) -> dict:
    """Run a git command and return structured result."""
    try:
        r = subprocess.run(["git"] + args, cwd=cwd,
                           capture_output=True, text=True, timeout=timeout)
        return {
            "success": r.returncode == 0,
            "stdout": r.stdout.strip(),
            "stderr": r.stderr.strip(),
            "returncode": r.returncode,
        }
    except FileNotFoundError:
        return {"success": False, "stderr": "git not found on this system", "returncode": -1}
    except Exception as e:
        return {"success": False, "stderr": str(e), "returncode": -2}


def git_init(project_dir: str | Path) -> dict:
    """Initialize a git repository in the project workdir."""
    d = str(project_dir)
    if os.path.isdir(os.path.join(d, ".git")):
        return {"success": True, "reason": "already initialized"}

    result = _run_git(["init"], d)
    if not result["success"]:
        return {"success": False, "error": result["stderr"]}

    gi = os.path.join(d, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w") as f:
            f.write(
                "# PaperPilot auto-generated\n"
                "__pycache__/\n"
                "*.pyc\n"
                ".venv/\n"
                "*.egg-info/\n"
                "build/\n"
                "dist/\n"
            )
    return {"success": True}


def git_checkpoint(project_dir: str | Path, message: str) -> dict:
    """Stage all changes and commit as a named checkpoint."""
    d = str(project_dir)
    escaped = message.replace('"', '\\"')

    _run_git(["add", "-A"], d)
    r = _run_git(["commit", "-m", escaped, "--allow-empty"], d)

    if r["returncode"] == 0:
        h = _run_git(["rev-parse", "--short", "HEAD"], d)
        return {"success": True, "hash": h["stdout"], "message": message}
    return {"success": True, "message": r["stdout"] or "no changes to commit", "hash": ""}


def git_log(project_dir: str | Path, n: int = 20) -> dict:
    """Return the last n commits as a formatted string."""
    d = str(project_dir)
    r = _run_git(["log", "--oneline", f"-{n}"], d)
    log_text = r["stdout"]
    entries = [e for e in log_text.split("\n") if e.strip()]
    return {"log": "\n".join(entries), "count": len(entries)}


def git_rollback(project_dir: str | Path, commit_hash: str) -> dict:
    """Hard reset the working tree to a given commit."""
    d = str(project_dir)
    r = _run_git(["reset", "--hard", commit_hash], d)
    if not r["success"]:
        return {"success": False, "error": r["stderr"] or "reset failed"}
    return {"success": True, "restored_to": commit_hash}


def git_status(project_dir: str | Path) -> dict:
    """Return the working tree status."""
    d = str(project_dir)
    r = _run_git(["status", "--short"], d)
    output = r["stdout"]
    return {"clean": output == "", "output": output}


def should_auto_checkpoint(action_names: list[str], has_errors: bool = False) -> bool:
    """Return True if this agent round should trigger a checkpoint."""
    if has_errors:
        return False
    write_actions = {"file_write", "file_edit", "shell_exec"}
    return bool(set(action_names) & write_actions)


# ── ToolSchema definitions ──

def _git_init_handler(params: dict, llm, state, emit) -> ToolResult:
    cwd = _get_project_git_dir(state)
    if not cwd:
        return ToolResult.fail("No project workspace directory")
    result = git_init(cwd)
    if result.get("success"):
        return ToolResult.ok(**result)
    return ToolResult.fail(result.get("error", "git init failed"))


def _git_checkpoint_handler(params: dict, llm, state, emit) -> ToolResult:
    cwd = _get_project_git_dir(state)
    if not cwd:
        return ToolResult.fail("No project workspace directory")
    message = params.get("message", params.get("description", "checkpoint"))
    result = git_checkpoint(cwd, message)
    if result.get("success"):
        return ToolResult.ok(**result)
    return ToolResult.fail(result.get("error", "checkpoint failed"))


def _git_log_handler(params: dict, llm, state, emit) -> ToolResult:
    cwd = _get_project_git_dir(state)
    if not cwd:
        return ToolResult.fail("No project workspace directory")
    n = params.get("limit", params.get("n", 20))
    result = git_log(cwd, int(n))
    return ToolResult.ok(**result)


def _git_rollback_handler(params: dict, llm, state, emit) -> ToolResult:
    cwd = _get_project_git_dir(state)
    if not cwd:
        return ToolResult.fail("No project workspace directory")
    commit_hash = params.get("commit", params.get("hash", ""))
    if not commit_hash:
        return ToolResult.fail("Missing commit hash")
    result = git_rollback(cwd, commit_hash)
    if result.get("success"):
        return ToolResult.ok(**result)
    return ToolResult.fail(result.get("error", "rollback failed"))


def _git_status_handler(params: dict, llm, state, emit) -> ToolResult:
    cwd = _get_project_git_dir(state)
    if not cwd:
        return ToolResult.fail("No project workspace directory")
    result = git_status(cwd)
    return ToolResult.ok(**result)


git_init_tool = ToolSchema(
    name="git_init",
    description="Initialize a git repository in the project workspace for version control.",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    handler=_git_init_handler,
    category="git",
)

git_checkpoint_tool = ToolSchema(
    name="git_checkpoint",
    description="Save a checkpoint (git commit) of the current workspace state. Call after important changes.",
    parameters={
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Commit message describing what changed (e.g. 'round_1: read GPT-4 paper')",
            },
        },
        "required": ["message"],
    },
    handler=_git_checkpoint_handler,
    category="git",
)

git_log_tool = ToolSchema(
    name="git_log",
    description="Show recent git commit history for the project workspace.",
    parameters={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Number of recent commits to show (default 20)",
            },
        },
        "required": [],
    },
    handler=_git_log_handler,
    category="git",
)

git_rollback_tool = ToolSchema(
    name="git_rollback",
    description="Restore the workspace to a previous checkpoint (git reset --hard). DANGER: overwrites unsaved work.",
    parameters={
        "type": "object",
        "properties": {
            "commit": {
                "type": "string",
                "description": "Git commit hash to restore to (from git_log output)",
            },
        },
        "required": ["commit"],
    },
    handler=_git_rollback_handler,
    category="git",
)

git_status_tool = ToolSchema(
    name="git_status",
    description="Show current workspace status — what files are modified, added, or untracked.",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    handler=_git_status_handler,
    category="git",
)
