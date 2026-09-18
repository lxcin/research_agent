"""Workspace checkpoints for shell-command rollback.

Sandboxing (see `research_agent.sandbox`) controls WHERE a command may write; it
mounts the workspace into the container, so writes land on the host directly.
This module answers the other half: *can those writes be undone?*

Before a mutating shell command runs, `create_checkpoint()` captures the full
workspace tree as a commit object **without touching the working tree or the real
index** (it uses a throwaway index via GIT_INDEX_FILE). The commit is kept
reachable under `refs/research-agent/checkpoints/*`, so `restore_checkpoint()`
can later put the workspace back exactly as it was.

Scope / known gaps:
  - Git-only (the workspace is assumed to be a repo, as with proposal.py). If the
    workspace is not a repo, create_checkpoint() is a no-op.
  - Untracked-but-not-ignored files ARE captured; git-ignored files are NOT.
  - restore_checkpoint() runs `git clean -fd`, which removes untracked files. It
    is an explicit, user-triggered action, never automatic.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import datetime, timezone

REF_PREFIX = "refs/research-agent/checkpoints"


def _run(args: list[str], ws: str, env: dict | None = None, timeout: int = 30):
    try:
        return subprocess.run(["git"] + args, cwd=ws, capture_output=True,
                              text=True, timeout=timeout, env=env)
    except Exception as e:  # noqa: BLE001 - caller inspects
        class _R:
            returncode, stdout, stderr = -1, "", str(e)
        return _R()


def is_repo(ws: str) -> bool:
    return bool(ws) and os.path.isdir(os.path.join(ws, ".git"))


def _has_head(ws: str) -> bool:
    return _run(["rev-parse", "--verify", "HEAD"], ws).returncode == 0


def checkpoint_enabled() -> bool:
    from research_agent.config import load_config
    return bool((load_config().get("shell") or {}).get("checkpoint", True))


def create_checkpoint(ws: str, label: str = "") -> dict:
    """Snapshot the workspace tree into a checkpoint ref. Never raises.

    Returns {"success", "ref", "sha", "reason"}.
    """
    if not is_repo(ws):
        return {"success": False, "reason": "workspace is not a git repo"}
    from research_agent.tools.git_tool import _ensure_git_config
    _ensure_git_config(ws)

    fd, idx = tempfile.mkstemp(prefix="ra-index-")
    os.close(fd)
    os.remove(idx)  # git wants to create it itself
    env = dict(os.environ, GIT_INDEX_FILE=idx)
    try:
        if _has_head(ws):
            _run(["read-tree", "HEAD"], ws, env)
        r = _run(["add", "-A"], ws, env)
        if r.returncode != 0:
            return {"success": False, "reason": r.stderr.strip() or "git add failed"}
        tree = _run(["write-tree"], ws, env).stdout.strip()
        if not tree:
            return {"success": False, "reason": "no tree produced"}

        msg = f"checkpoint {label}".strip()
        args = ["commit-tree", tree, "-m", msg]
        if _has_head(ws):
            args += ["-p", "HEAD"]
        c = _run(args, ws, env)
        if c.returncode != 0:
            return {"success": False, "reason": c.stderr.strip() or "commit-tree failed"}
        sha = c.stdout.strip()

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        ref = f"{REF_PREFIX}/{ts}"
        _run(["update-ref", ref, sha], ws)
        return {"success": True, "ref": ref, "sha": sha}
    finally:
        try:
            os.remove(idx)
        except OSError:
            pass


def list_checkpoints(ws: str, limit: int = 20) -> list[dict]:
    if not is_repo(ws):
        return []
    r = _run(["for-each-ref", "--sort=-refname",
              "--format=%(refname)\t%(objectname:short)\t%(subject)",
              REF_PREFIX], ws)
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            out.append({"ref": parts[0], "sha": parts[1],
                        "label": parts[2] if len(parts) > 2 else ""})
    return out[:limit]


def restore_checkpoint(ws: str, ref: str) -> dict:
    """Restore the workspace to the checkpoint tree. Never raises."""
    if not is_repo(ws):
        return {"success": False, "reason": "workspace is not a git repo"}
    # Resolve ref -> sha (accept short ref/sha too).
    resolved = _run(["rev-parse", "--verify", f"{ref}^{{commit}}"], ws)
    sha = resolved.stdout.strip()
    if resolved.returncode != 0 or not sha:
        return {"success": False, "reason": f"unknown checkpoint: {ref}"}

    r = _run(["checkout", sha, "--", "."], ws)
    if r.returncode != 0:
        return {"success": False, "reason": r.stderr.strip() or "checkout failed"}
    _run(["clean", "-fd"], ws)          # drop files not present in the snapshot
    if _has_head(ws):
        _run(["reset", "-q"], ws)       # unstage; working tree left at snapshot
    return {"success": True, "restored": sha}
