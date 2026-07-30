"""Git integration tools — experiment version management and checkpoints.
NOT exposed to LLM. All git operations are internal:
  - git_init: called automatically at project creation
  - git_checkpoint: called automatically by auto-checkpoint after each round
  - should_auto_checkpoint: determines if a round should trigger a checkpoint
LLM achieves git operations via shell_exec (git log/status/reset)."""
import os
import subprocess
from pathlib import Path


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


def _ensure_git_config(project_dir: str):
    """Ensure git user.name and user.email are set for this repo."""
    for key, val in [("user.name", "PaperPilot"), ("user.email", "paperpilot@agent.local")]:
        r = _run_git(["config", key], str(project_dir))
        if not r["stdout"]:
            _run_git(["config", key, val], str(project_dir))


def git_init(project_dir: str | Path) -> dict:
    """Initialize a git repository in the project workdir."""
    d = str(project_dir)
    if os.path.isdir(os.path.join(d, ".git")):
        return {"success": True, "reason": "already initialized"}

    result = _run_git(["init"], d)
    if not result["success"]:
        return {"success": False, "error": result["stderr"]}

    _ensure_git_config(d)

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
    _ensure_git_config(d)
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
