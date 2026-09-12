"""File-change proposals — git-based staging with per-file keep / undo.

Agent file edits land in the workspace (a git repo) but are NOT committed during
the run. After a turn, `collect()` reports the changed files as a diff proposal;
the user then keeps (commit) or undoes (restore/delete) per file, or all at once.

This is the "opencode/Claude Code" interaction: the agent works normally, changes
are shown as a reviewable diff, and nothing is finalized until the user agrees.

If the workspace is not a git repo, collect() returns [] and keep/undo no-op
(the caller falls back to the plain "changes already written" behavior).
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Iterable

from research_agent.tools.git_tool import _run_git, _ensure_git_config


def _git_raw(args: list[str], cwd: str, timeout: int = 15) -> str:
    """Run git and return raw (un-stripped) stdout.

    `git status --porcelain` lines begin with a status space that a naive
    .strip() would remove, shifting the path; porcelain needs exact output.
    """
    try:
        r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                           text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ""


@dataclass
class Change:
    path: str            # workspace-relative
    status: str          # "added" | "modified" | "deleted"
    additions: int = 0
    deletions: int = 0
    diff: str = ""

    def summary(self) -> str:
        sign = {"added": "+", "deleted": "-", "modified": "~"}.get(self.status, "?")
        return f"{sign} {self.path}  +{self.additions} -{self.deletions}"


class ProposalManager:
    def __init__(self, workspace: str):
        self.workspace = workspace

    # ── repo helpers ──

    def is_repo(self) -> bool:
        return bool(self.workspace) and os.path.isdir(os.path.join(self.workspace, ".git"))

    def has_head(self) -> bool:
        if not self.is_repo():
            return False
        return _run_git(["rev-parse", "--verify", "HEAD"], self.workspace)["success"]

    def _status_map(self) -> dict[str, str]:
        """path -> status from `git status --porcelain` (raw, space-preserving)."""
        out: dict[str, str] = {}
        if not self.is_repo():
            return out
        raw = _git_raw(["status", "--porcelain"], self.workspace)
        for line in raw.splitlines():
            if len(line) < 4:
                continue
            code, path = line[:2], line[3:]
            if path.startswith('"') and path.endswith('"'):
                path = path[1:-1]
            if " -> " in path:  # rename
                path = path.split(" -> ")[-1].strip('"')
            if code == "??":
                out[path] = "added"
            elif "D" in code:
                out[path] = "deleted"
            elif "A" in code:
                out[path] = "added"
            else:
                out[path] = "modified"
        return out

    def _numstat(self, path: str) -> tuple[int, int]:
        args = ["diff", "--numstat"]
        if self.has_head():
            args = ["diff", "HEAD", "--numstat", "--", path]
        else:
            args = ["diff", "--numstat", "--", path]
        r = _run_git(args, self.workspace)
        if r["success"] and r["stdout"]:
            parts = r["stdout"].splitlines()[0].split("\t")
            try:
                return int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                pass
        return 0, 0

    def _file_diff(self, path: str, status: str) -> str:
        if status == "added":
            # untracked: synthesize a diff (git diff shows nothing without -N)
            full = os.path.join(self.workspace, path)
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    body = f.read()
            except OSError:
                return f"--- /dev/null\n+++ b/{path}\n(new file)"
            lines = body.splitlines()[:200]
            added = "".join(f"+{ln}\n" for ln in lines)
            return f"--- /dev/null\n+++ b/{path}\n@@ new file @@\n{added}"
        base = "HEAD" if self.has_head() else ""
        r = _run_git(["diff", base, "--", path] if base else ["diff", "--", path],
                     self.workspace)
        return r["stdout"] if r["success"] else ""

    # ── public API ──

    def collect(self, paths: Iterable[str] | None = None) -> list[Change]:
        """Return the changed files (optionally filtered) as diff proposals."""
        if not self.is_repo():
            return []
        status_map = self._status_map()
        wanted = set(paths) if paths else None
        changes: list[Change] = []
        for path, status in sorted(status_map.items()):
            if wanted is not None and path not in wanted:
                continue
            adds, dels = self._numstat(path)
            if status == "added":
                full = os.path.join(self.workspace, path)
                try:
                    with open(full, "r", encoding="utf-8", errors="replace") as f:
                        adds = len(f.read().splitlines())
                except OSError:
                    adds = adds
                dels = 0
            changes.append(Change(path=path, status=status,
                                  additions=adds, deletions=dels,
                                  diff=self._file_diff(path, status)))
        return changes

    def keep(self, paths: Iterable[str], message: str = "chore: accept agent changes") -> dict:
        """Commit the given files (accept the proposal)."""
        if not self.is_repo():
            return {"success": False, "reason": "not a git repo"}
        paths = list(paths)
        if not paths:
            return {"success": True, "kept": 0}
        _ensure_git_config(self.workspace)
        _run_git(["add", "--"] + paths, self.workspace)
        msg = message.replace('"', '\\"')
        r = _run_git(["commit", "-m", msg, "--"] + paths, self.workspace)
        return {"success": r["success"], "kept": len(paths),
                "stdout": r["stdout"], "stderr": r["stderr"]}

    def undo(self, paths: Iterable[str]) -> dict:
        """Discard the given files' changes (reject the proposal)."""
        if not self.is_repo():
            return {"success": False, "reason": "not a git repo"}
        paths = list(paths)
        if not paths:
            return {"success": True, "undone": 0}
        status_map = self._status_map()
        undone = 0
        for path in paths:
            status = status_map.get(path)
            _run_git(["reset", "--", path], self.workspace)  # unstage if staged
            if status == "added":
                # untracked new file → delete
                full = os.path.join(self.workspace, path)
                try:
                    if os.path.isfile(full):
                        os.remove(full)
                    undone += 1
                except OSError:
                    pass
            else:
                # tracked modified/deleted → restore to HEAD
                if self.has_head():
                    r = _run_git(["checkout", "HEAD", "--", path], self.workspace)
                    if r["success"]:
                        undone += 1
        return {"success": True, "undone": undone}

    def keep_all(self, message: str = "chore: accept agent changes") -> dict:
        return self.keep([c.path for c in self.collect()], message)

    def undo_all(self) -> dict:
        return self.undo([c.path for c in self.collect()])
