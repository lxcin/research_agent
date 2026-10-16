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
                           text=True, errors="replace", timeout=timeout)
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
    review_token: str = ""
    non_reversible: tuple[str, ...] = ("Git 忽略文件不在备份范围", "远程 API、推送及数据库副作用需人工补偿")

    def summary(self) -> str:
        sign = {"added": "+", "deleted": "-", "modified": "~"}.get(self.status, "?")
        return f"{sign} {self.path}  +{self.additions} -{self.deletions}"


def workspace_mutation(method):
    from functools import wraps
    @wraps(method)
    def call(self, *args, **kwargs):
        from research_agent.execution import exclusive_request
        try:
            from research_agent.docker_ownership import reconcile_before_request
            reconcile_before_request()
            from research_agent.background_tasks import recover_workspace
            recover_workspace(self.workspace)
            with exclusive_request(self.workspace, "proposal:" + self.workspace):
                return method(self, *args, **kwargs)
        except (RuntimeError, ValueError) as exc:
            return {"success": False, "reason": str(exc)}
    return call


class ProposalManager:
    def __init__(self, workspace: str):
        self.workspace = workspace
        from research_agent.review_baseline import baseline
        self._reviewed = baseline(workspace, "proposal")
        self._review_meta = baseline(workspace, "proposal_meta")
        self.review_token = self._review_meta.get("token", "")

    def begin_execution(self):
        from research_agent.review_baseline import baseline
        baseline(self.workspace, "execution_dirty", {"paths": list(self._status_map())})

    def _fingerprint(self, path):
        from research_agent.review_baseline import fingerprint
        return fingerprint(self.workspace, path)

    def _save_review(self):
        from research_agent.review_baseline import baseline
        baseline(self.workspace, "proposal", self._reviewed)
        if not self._reviewed:
            baseline(self.workspace, "proposal_meta", {})
            baseline(self.workspace, "proposal_head", {})
            self.review_token = ""

    def _conflicts(self, paths):
        from research_agent.review_baseline import baseline
        head = baseline(self.workspace, "proposal_head")
        if self._reviewed and head and head.get("sha") != _run_git(["rev-parse", "HEAD"], self.workspace)["stdout"]:
            return list(paths)
        return [path for path in paths if path in self._reviewed and
                self._reviewed[path] != self._fingerprint(path)]

    # ── repo helpers ──

    def is_repo(self) -> bool:
        return bool(self.workspace) and os.path.exists(os.path.join(self.workspace, ".git"))

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

    def collect(self, paths: Iterable[str] | None = None, *, _host_locked=False) -> list[Change]:
        """Return the changed files (optionally filtered) as diff proposals."""
        if not self.is_repo():
            return []
        from research_agent.review_baseline import baseline
        active = baseline(self.workspace, "active-turn")
        if active:
            return self._collect_turn(active, paths, _host_locked=_host_locked)
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
        for change in changes:
            self._reviewed.setdefault(change.path, self._fingerprint(change.path))
        from research_agent.review_baseline import baseline
        if changes and not self.review_token:
            import uuid
            self.review_token = uuid.uuid4().hex
            dirty = baseline(self.workspace, "execution_dirty")
            self._review_meta = {"token": self.review_token, "dirty": dirty.get("paths"),
                                 "head": _run_git(["rev-parse", "HEAD"], self.workspace)["stdout"]}
            baseline(self.workspace, "proposal_meta", self._review_meta)
        for change in changes:
            change.review_token = self.review_token
        if changes and not baseline(self.workspace, "proposal_head"):
            baseline(self.workspace, "proposal_head", {"sha": _run_git(["rev-parse", "HEAD"], self.workspace)["stdout"]})
        self._save_review()
        return changes

    def _collect_turn(self, active, paths, *, _host_locked=False):
        """Show S→E, rather than conflating preexisting edits with HEAD differences."""
        import difflib
        from research_agent.review_baseline import baseline
        from research_agent.rollback import _read_version, review_undo, _review_undo_locked
        sha = active["sha"]
        start, end = baseline(self.workspace, "checkpoint-start:" + sha), baseline(self.workspace, "checkpoint:" + sha)
        if not start or not end.get("snapshot_sha"):
            return []
        before = start["state"]
        progress = baseline(self.workspace, "undo-progress")
        done = set(progress.get("paths", []))
        wanted = set(paths) if paths is not None else None
        changes = []
        for path in sorted(before["files"].keys() | end["files"].keys()):
            if path in done or wanted is not None and path not in wanted:
                continue
            if (before["files"].get(path), before["modes"].get(path)) == (end["files"].get(path), end["modes"].get(path)):
                continue
            s = _read_version(self.workspace, sha, before, path)
            e = _read_version(self.workspace, end["snapshot_sha"], end, path)
            status = "added" if s is None else "deleted" if e is None else "modified"
            old, new = (s or b"").decode("utf-8", "replace").splitlines(True), (e or b"").decode("utf-8", "replace").splitlines(True)
            diff = list(difflib.unified_diff(old, new, fromfile="before/" + path, tofile="agent-end/" + path))
            changes.append(Change(path, status, sum(line.startswith("+") and not line.startswith("+++") for line in diff),
                                  sum(line.startswith("-") and not line.startswith("---") for line in diff), "".join(diff)[:8000]))
        review_fn = _review_undo_locked if _host_locked else review_undo
        review = review_fn(self.workspace, active["ref"], [c.path for c in changes]) if changes else {}
        self.review_token = review.get("review_token", "") if review.get("success") else ""
        self._reviewed = {c.path: self._fingerprint(c.path) for c in changes}
        baseline(self.workspace, "proposal", self._reviewed)
        baseline(self.workspace, "proposal_meta", {"token": self.review_token, "turn": sha})
        baseline(self.workspace, "proposal_head", {"sha": end["head"]})
        for change in changes:
            change.review_token = self.review_token
        return changes

    def review_undo(self, paths=None, *, resolved=()):
        """Explicitly review a selected subset or edited conflict result."""
        from research_agent.review_baseline import baseline
        from research_agent.rollback import review_undo
        active = baseline(self.workspace, "active-turn")
        if not active:
            return {"success": False, "reason": "Turn snapshot required for selective undo"}
        result = review_undo(self.workspace, active["ref"], paths, resolved=resolved)
        if result["success"]:
            self.review_token = result["review_token"]
            self._reviewed = {path: self._fingerprint(path) for path in result["paths"]}
        return result

    @workspace_mutation
    def keep(self, paths: Iterable[str], message: str = "chore: accept agent changes") -> dict:
        """Commit the given files (accept the proposal)."""
        if not self.is_repo():
            return {"success": False, "reason": "not a git repo"}
        paths = list(paths)
        conflicts = self._conflicts(paths)
        if conflicts:
            return {"success": False, "reason": "files changed since review", "conflicts": conflicts}
        if not paths:
            return {"success": True, "kept": 0}
        _ensure_git_config(self.workspace)
        _run_git(["add", "--"] + paths, self.workspace)
        msg = message.replace('"', '\\"')
        r = _run_git(["commit", "-m", msg, "--"] + paths, self.workspace)
        if r["success"]:
            from research_agent.review_baseline import baseline
            baseline(self.workspace, "proposal_head", {"sha": _run_git(["rev-parse", "HEAD"], self.workspace)["stdout"]})
            for path in paths:
                self._reviewed.pop(path, None)
            self._save_review()
        return {"success": r["success"], "kept": len(paths),
                "stdout": r["stdout"], "stderr": r["stderr"]}

    @workspace_mutation
    def undo(self, paths: Iterable[str], *, review_token: str | None = None) -> dict:
        """Discard the given files' changes (reject the proposal)."""
        if not self.is_repo():
            return {"success": False, "reason": "not a git repo"}
        paths = list(paths)
        from research_agent.review_baseline import baseline
        if baseline(self.workspace, "active-turn"):
            from research_agent.rollback import _apply_undo
            plan = baseline(self.workspace, "selective-undo")
            if sorted(set(paths)) != plan.get("paths"):
                return {"success": False, "reason": "Review this exact file selection before undo"}
            # Already inside workspace_mutation; use the same lock context, not a nested lock.
            return _apply_undo(self.workspace, review_token=review_token, _locked=True)
        current_meta = baseline(self.workspace, "proposal_meta")
        if not review_token or review_token != current_meta.get("token") or any(p not in self._reviewed for p in paths):
            return {"success": False, "reason": "valid review token and end baseline required"}
        if current_meta.get("dirty") is None:
            return {"success": False, "reason": "execution-start baseline missing"}
        mixed = sorted(set(paths).intersection(current_meta["dirty"]))
        if mixed:
            return {"success": False, "reason": "preexisting human changes; reverse patch required", "conflicts": mixed}
        conflicts = self._conflicts(paths)
        if conflicts:
            return {"success": False, "reason": "files changed since review", "conflicts": conflicts}
        if not paths:
            return {"success": True, "undone": 0}
        for path in paths:
            full = os.path.join(self.workspace, path)
            if os.path.islink(full):
                return {"success": False, "reason": "symlink rollback requires manual review", "conflicts": [path]}
            if os.path.isfile(full):
                with open(full, "rb") as stream:
                    if b"\0" in stream.read():
                        return {"success": False, "reason": "binary rollback requires manual review", "conflicts": [path]}
        from research_agent.checkpoint import create_checkpoint
        backup = create_checkpoint(self.workspace, label="before-proposal-undo", dedupe=False)
        if not backup.get("success"):
            return {"success": False, "reason": "could not preserve undo backup"}
        status_map = self._status_map()
        undone = 0
        for path in paths:
            status = status_map.get(path)
            if self._conflicts([path]) or os.path.islink(os.path.join(self.workspace, path)):
                return {"success": False, "reason": "file changed before write", "conflicts": [path], "backup": backup.get("ref")}
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
                    r = subprocess.run(["git", "cat-file", "--filters", "HEAD:" + path], cwd=self.workspace, capture_output=True)
                    if r.returncode == 0:
                        if b"\0" in r.stdout:
                            return {"success": False, "reason": "binary rollback requires manual review", "conflicts": [path], "backup": backup.get("ref")}
                        import tempfile, hashlib
                        target = os.path.join(self.workspace, path)
                        descriptor, temporary = tempfile.mkstemp(dir=os.path.dirname(target))
                        try:
                            with os.fdopen(descriptor, "wb") as stream:
                                stream.write(r.stdout)
                                stream.flush()
                                os.fsync(stream.fileno())
                            if self._conflicts([path]):
                                return {"success": False, "reason": "file changed during preparation", "backup": backup.get("ref")}
                            if os.path.exists(target):
                                import stat
                                os.chmod(temporary, stat.S_IMODE(os.stat(target).st_mode))
                            os.replace(temporary, target)
                            if self._fingerprint(path) != hashlib.sha256(r.stdout).hexdigest():
                                return {"success": False, "reason": "file changed after replacement", "backup": backup.get("ref")}
                            undone += 1
                        finally:
                            if os.path.exists(temporary):
                                os.unlink(temporary)
        for path in paths:
            self._reviewed.pop(path, None)
        self._save_review()
        _run_git(["update-index", "--refresh"], self.workspace)
        return {"success": undone == len(paths), "undone": undone, "backup": backup.get("ref")}

    def keep_all(self, message: str = "chore: accept agent changes") -> dict:
        paths = list(self._reviewed) if self._reviewed else [c.path for c in self.collect()]
        return self.keep(paths, message)

    def undo_all(self, *, review_token: str | None = None) -> dict:
        return self.undo(list(self._reviewed), review_token=review_token)
