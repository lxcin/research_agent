"""Reviewed, latest-turn, selective reverse merge. All mutations belong to the host."""
from __future__ import annotations

import base64
from contextlib import nullcontext
from dataclasses import dataclass
import difflib
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import uuid

from research_agent import checkpoint as cp
from research_agent.review_baseline import baseline, fingerprint


@dataclass(frozen=True)
class UndoRequest:
    ref: str
    paths: tuple[str, ...] | None = None
    resolved: tuple[str, ...] = ()


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _blob(ws, sha, path):
    result = subprocess.run(["git", "cat-file", "blob", sha + ":" + path], cwd=ws,
                            capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError("Snapshot content missing: " + path)
    return result.stdout


def _read_version(ws, sha, state, path):
    digest = state["files"].get(path)
    if digest is None:
        return None
    data = _blob(ws, sha, path)
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Snapshot raw content mismatch: " + path)
    return data


def _text(data):
    if b"\0" in data or len(data) > 2_000_000:
        raise ValueError("Binary or oversized text requires manual resolution")
    data.decode("utf-8", "strict")


def _merge(current, end, start):
    # Operate on bytes: preserve CRLF/BOM/no-final-newline without decoding writes.
    for data in (current, end, start):
        _text(data)
    with tempfile.TemporaryDirectory(prefix="ra-merge-") as tmp:
        paths = [Path(tmp) / name for name in ("current", "agent-end", "before")]
        for path, data in zip(paths, (current, end, start)):
            path.write_bytes(data)
        result = subprocess.run(["git", "merge-file", "-p", "--diff3", "-L", "current",
                                 "-L", "agent-end", "-L", "before", *map(str, paths)],
                                capture_output=True, timeout=30)
        if result.returncode < 0 or result.returncode > 127:
            raise ValueError("Text merge failed")
        return result.stdout, result.returncode != 0


def _index_entries(ws, index, env):
    if index["data"] is None:
        return {}
    Path(env["GIT_INDEX_FILE"]).write_bytes(base64.b64decode(index["data"]))
    result = cp._run(["ls-files", "--stage", "-z"], ws, env)
    flags = cp._run(["ls-files", "-v", "-z"], ws, env)
    debug = cp._run(["ls-files", "--debug"], ws, env)
    shared = cp._run(["rev-parse", "--shared-index-path"], ws, env)
    if result.returncode or flags.returncode or debug.returncode or shared.returncode:
        raise ValueError("Cannot inspect snapshot index")
    if shared.stdout.strip() or any(int(value, 16) for value in re.findall(r"^  size: \d+\s+flags: ([0-9a-fA-F]+)$", debug.stdout, re.MULTILINE)):
        raise ValueError("Extended/split index flags require manual handling")
    if any(entry[0] != "H" for entry in flags.stdout.split("\0") if entry):
        raise ValueError("Special index flags require manual handling")
    entries = {}
    for entry in filter(None, result.stdout.split("\0")):
        meta, path = entry.split("\t", 1)
        mode, oid, stage = meta.split()
        if stage != "0" or mode not in {"100644", "100755"} or not oid.strip("0"):
            raise ValueError("Conflicted/special index entries require manual handling")
        entries[path] = (mode, oid)
    return entries


def _selected_index(ws, start_index, current_index, paths):
    if start_index["hash"] == current_index["hash"]:
        return current_index
    with tempfile.TemporaryDirectory(prefix="ra-index-") as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "before"))
        before = _index_entries(ws, start_index, env)
        env["GIT_INDEX_FILE"] = str(Path(tmp) / "current")
        current = _index_entries(ws, current_index, env)
        if all(before.get(path) == current.get(path) for path in paths):
            return current_index
        if current_index["data"] is None:
            result = cp._run(["read-tree", "--empty"], ws, env)
            if result.returncode:
                raise ValueError("Could not initialize temporary index")
        for path in paths:
            result = cp._run(["update-index", "--force-remove", "--", path], ws, env)
            if result.returncode:
                raise ValueError("Could not remove selected index entry")
            if path in before:
                mode, oid = before[path]
                result = cp._run(["update-index", "--add", "--cacheinfo", mode, oid, path], ws, env)
                if result.returncode:
                    raise ValueError("Could not restore selected index entry")
        data = Path(env["GIT_INDEX_FILE"]).read_bytes()
        return dict(current_index, data=base64.b64encode(data).decode(), hash=hashlib.sha256(data).hexdigest())


def _pin_results(ws, plan_id, changes):
    """Pin candidate blobs in Git, rather than duplicating file bodies in SQLite."""
    with tempfile.TemporaryDirectory(prefix="ra-undo-") as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
        if cp._run(["read-tree", "--empty"], ws, env).returncode:
            raise ValueError("Could not create candidate tree")
        for change in changes:
            data = change.pop("data")
            if data is None:
                continue
            result = subprocess.run(["git", "hash-object", "-w", "--stdin", "--no-filters"], cwd=ws,
                                    input=data, capture_output=True, timeout=30)
            if result.returncode:
                raise ValueError("Could not preserve candidate content")
            mode = "100755" if change["mode"] & 0o111 else "100644"
            if cp._run(["update-index", "--add", "--cacheinfo", mode, result.stdout.decode().strip(), change["path"]], ws, env).returncode:
                raise ValueError("Could not preserve candidate entry")
        tree = cp._run(["write-tree"], ws, env)
        if tree.returncode:
            raise ValueError("Could not preserve candidate tree")
        commit = cp._run(["commit-tree", tree.stdout.strip(), "-m", "reviewed undo " + plan_id], ws, env)
        if commit.returncode:
            raise ValueError("Could not preserve candidate snapshot")
        ref = "refs/research-agent/undo/" + cp._owner(ws) + "/" + plan_id
        cp._require(["update-ref", ref, commit.stdout.strip()], ws)
        return ref, commit.stdout.strip()


def _artifact(ws, path, versions, merged):
    from research_agent.config import get_data_dir
    root = get_data_dir().resolve() / "undo-artifacts" / cp._owner(ws)
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="conflict-", dir=root))
    for name, data in zip(("before", "agent-end", "current", "result"), (*versions, merged)):
        (directory / name).write_bytes(data or b"")
    owned = baseline(ws, "undo-artifact-files")
    owned.setdefault("directories", []).append(str(directory))
    baseline(ws, "undo-artifact-files", owned)
    return {"path": path, "directory": str(directory), "result": str(directory / "result")}


def _token(plan, current, lock, attempt):
    return _hash({"plan": plan, "current": current, "lock": lock, "attempt": attempt})


def _resume_plan(ws, stored, current, journal):
    expected, desired = stored["expected"], stored["desired"]
    for path in current["files"].keys() | expected["files"].keys() | desired["files"].keys():
        actual = (current["files"].get(path), current["modes"].get(path))
        allowed = {(state["files"].get(path), state["modes"].get(path)) for state in (expected, desired)}
        if actual not in allowed:
            raise ValueError("File changed outside the pending undo: " + path)
    if current["head"] != expected["head"] or current["index"] not in {expected["index"], desired["index"]}:
        raise ValueError("HEAD/index changed outside the pending undo")
    return stored


def _prepare(ws, stored, current, journal):
    index = cp._index_state(ws)
    if stored["index"]["path"] != index["path"]:
        raise ValueError("Undo index belongs to another worktree")
    lock = cp._lock_state(index["path"] + ".lock")
    resume = journal.get("kind") == "selective" and journal.get("status") in {"applying", "partial"}
    if not resume and current != stored["expected"]:
        raise ValueError("Workspace/index changed while preparing undo; review again")
    if lock is not None:
        owned = journal.get("index_lock", {})
        if not (resume and owned.get("identity") == lock["identity"] and
                lock["hash"] in {owned.get("marker_hash"), stored["index"]["hash"]}):
            raise ValueError("Foreign or damaged index lock requires manual inspection")
    if cp._require(["rev-parse", "--verify", stored["candidate_ref"]], ws).strip() != stored["candidate_sha"]:
        raise ValueError("Undo candidate reference changed")
    changes = []
    for change in stored["changes"]:
        path = change["path"]
        # Includes ignored collisions not listed by _state.
        if fingerprint(ws, path) != current["files"].get(path):
            raise ValueError("Ignored or external file occupies undo path: " + path)
        if (current["files"].get(path), current["modes"].get(path)) == (stored["desired"]["files"].get(path), stored["desired"]["modes"].get(path)):
            continue
        data = _read_version(ws, stored["candidate_sha"], stored["desired"], path)
        changes.append(dict(change, data=data))
    token = _token(stored, current, lock, journal.get("attempt_backup") if resume else None)
    return {"success": True, "kind": "selective", "sha": stored["sha"], "plan_id": stored["plan_id"],
            "start": {"state": stored["desired"], "index": stored["index"]},
            "current": current, "changes": changes, "review_token": token, "resume": resume,
            "index_lock": lock, "stale_lock": lock is not None,
            "original_backup": journal.get("backup") if resume else None}


def _plan(ws, request):
    sha = cp._resolve_owned(ws, request.ref)
    active = baseline(ws, "active-turn")
    if not active or active.get("sha") != sha:
        raise ValueError("Only the current turn can be undone")
    start, end = baseline(ws, "checkpoint-start:" + sha), baseline(ws, "checkpoint:" + sha)
    if start.get("version") != 2 or not end.get("snapshot_sha"):
        raise ValueError("Complete raw start/end snapshots required")
    if cp._require(["rev-parse", "--verify", end["snapshot_ref"]], ws).strip() != end["snapshot_sha"]:
        raise ValueError("End snapshot reference changed")
    current, journal = cp._state(ws), baseline(ws, "checkpoint-rollback")
    stored = baseline(ws, "selective-undo")
    # Complete journal is authoritative if the process died before publishing progress.
    if journal.get("status") == "complete" and journal.get("kind") == "selective" and journal.get("plan_id") == stored.get("plan_id"):
        progress = baseline(ws, "undo-progress")
        baseline(ws, "undo-progress", {"sha": sha, "paths": sorted(set(progress.get("paths", [])) | set(stored["paths"])),
                                      "index": stored["desired"]["index"]})
    if journal.get("status") in {"applying", "partial"}:
        if journal.get("kind") != "selective" or journal.get("target") != sha or stored.get("sha") != sha:
            raise ValueError("Another rollback requires recovery first")
        if request.resolved or request.paths is not None and sorted(request.paths) != stored["paths"]:
            raise ValueError("Resume the exact pending selection; do not recompute it")
        return _resume_plan(ws, stored, current, journal), journal
    before = start["state"]
    if before["head"] != end["head"] or current["head"] != end["head"]:
        raise ValueError("HEAD changed; undo does not rewrite commit history")
    changed = {path for path in before["files"].keys() | end["files"].keys()
               if (before["files"].get(path), before["modes"].get(path)) != (end["files"].get(path), end["modes"].get(path))}
    # Include pure staging changes even if the working file returned to its initial content.
    index = cp._index_state(ws)
    progress = baseline(ws, "undo-progress")
    if progress and progress.get("sha") != sha:
        raise ValueError("Undo progress belongs to another execution")
    if current["index"] != progress.get("index", end["index"]):
        raise ValueError("Index changed after execution; preserve human staging")
    with tempfile.TemporaryDirectory(prefix="ra-inspect-") as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "s"))
        s_entries = _index_entries(ws, start["index"], env)
        env["GIT_INDEX_FILE"] = str(Path(tmp) / "e")
        e_entries = _index_entries(ws, end["index_data"], env)
        changed |= {p for p in s_entries.keys() | e_entries.keys() if s_entries.get(p) != e_entries.get(p)}
    done = set(progress.get("paths", []))
    paths = sorted(changed - done if request.paths is None else set(request.paths))
    if not set(paths).issubset(changed - done):
        raise ValueError("Selection includes unchanged or already undone files")
    if not set(request.resolved).issubset(paths):
        raise ValueError("Resolved paths must belong to the selection")
    current_index = index
    target_index = _selected_index(ws, start["index"], current_index, paths)
    signature = _hash({"sha": sha, "current": current, "paths": paths})
    previous_conflicts = baseline(ws, "selective-conflicts")
    desired = json.loads(json.dumps(current))
    changes, conflicts, files = [], [], []
    for path in paths:
        target = cp._target(ws, path)
        if fingerprint(ws, path) != current["files"].get(path):
            raise ValueError("Ignored or external file occupies undo path: " + path)
        s = _read_version(ws, sha, before, path)
        e = _read_version(ws, end["snapshot_sha"], end, path)
        c = target.read_bytes() if target.exists() else None
        sm, em, cm = (state["modes"].get(path) for state in (before, end, current))
        conflict = False
        if (c, cm) == (s, sm) or (s, sm) == (e, em):
            result, mode = c, cm
        elif (c, cm) == (e, em):
            result, mode = s, sm
        elif s is None or e is None or c is None or (sm != em and cm not in {sm, em}):
            result, mode, conflict = c, cm, True
        else:
            mode = cm if sm == em else sm
            try:
                result, conflict = _merge(c, e, s)
            except (ValueError, UnicodeError):
                result, conflict = c, True
        if path in request.resolved:
            if previous_conflicts.get("signature") != signature:
                raise ValueError("Conflict inputs changed; generate a fresh conflict review")
            artifact = next((a for a in previous_conflicts.get("artifacts", []) if a["path"] == path), None)
            if not artifact or s is None or e is None or c is None:
                raise ValueError("Only a previously reviewed text conflict can be resolved this way")
            for data in (s, e, c):
                _text(data)
            candidate = Path(artifact["result"])
            from research_agent.config import get_data_dir
            root = get_data_dir().resolve() / "undo-artifacts" / cp._owner(ws)
            if candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
                raise ValueError("Invalid conflict result path")
            result = candidate.read_bytes()
            _text(result)
            if any(marker in result for marker in (b"<<<<<<<", b"|||||||", b"=======", b">>>>>>>")):
                raise ValueError("Conflict markers remain; finish the resolution before review")
            conflict = False
        if conflict:
            artifact = _artifact(ws, path, (s, e, c), result)
            conflicts.append(artifact)
            continue
        digest = hashlib.sha256(result).hexdigest() if result is not None else None
        desired["files"][path], desired["modes"][path] = digest, mode
        if (c, cm) != (result, mode):
            changes.append({"path": path, "mode": mode, "data": result})
        diff, unavailable = "", False
        try:
            for data in (c or b"", result or b""):
                _text(data)
            diff = "".join(difflib.unified_diff((c or b"").decode("utf-8").splitlines(True),
                          (result or b"").decode("utf-8").splitlines(True), fromfile="current/" + path, tofile="undo/" + path))
        except (UnicodeError, ValueError):
            unavailable = True
        files.append({"path": path, "action": "delete" if result is None else "restore-or-merge",
                      "diff": diff[:8000], "diff_truncated": len(diff) > 8000,
                      "preview_unavailable": unavailable, "target_hash": digest})
    if conflicts:
        baseline(ws, "selective-conflicts", {"signature": signature, "artifacts": conflicts})
        token = _hash({"signature": signature, "conflicts": conflicts})
        baseline(ws, "checkpoint-rollback", {"kind": "selective", "status": "conflict", "target": sha, "review_token": token})
        return {"conflicts": conflicts, "success": False, "reason": "Conflicts require explicit resolution or a new selection",
                "review_token": token}, journal
    desired["index"] = target_index["hash"]
    plan_id = uuid.uuid4().hex
    ref, candidate_sha = _pin_results(ws, plan_id, changes)
    new = {"version": 1, "plan_id": plan_id, "sha": sha, "paths": paths,
           "expected": current, "desired": desired, "index": target_index, "changes": changes,
           "candidate_ref": ref, "candidate_sha": candidate_sha, "files": files,
           "resolved": list(request.resolved), "resolution_signature": signature}
    # Refreshes re-use identical candidates/tokens; resolving edits creates a new immutable plan.
    if stored and all(stored.get(k) == new.get(k) for k in ("sha", "paths", "expected", "desired", "index", "files", "resolved")):
        cp._require(["update-ref", "-d", ref, candidate_sha], ws)
        return stored, journal
    baseline(ws, "selective-undo", new)
    if stored.get("candidate_ref"):
        cp._require(["update-ref", "-d", stored["candidate_ref"], stored["candidate_sha"]], ws)
    if journal.get("status") == "conflict":
        baseline(ws, "checkpoint-rollback", {})
    return new, journal


def review_undo(ws: str, ref: str, paths=None, *, resolved=()) -> dict:
    """Generate an immutable candidate; conflict results live outside the workspace."""
    from research_agent.execution import exclusive_request
    try:
        cp._admit_restore(ws)
        with exclusive_request(ws, "rollback:" + ws):
            return _review_undo_locked(ws, ref, paths, resolved=resolved)
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError, sqlite3.Error) as exc:
        return {"success": False, "reason": str(exc)}


def _review_undo_locked(ws, ref, paths=None, *, resolved=()):
    """Internal host entry while its request lease is held."""
    stored, journal = _plan(ws, UndoRequest(ref, tuple(paths) if paths is not None else None, tuple(resolved)))
    if stored.get("success") is False:
        return stored
    prepared = _prepare(ws, stored, cp._state(ws), journal)
    return {"success": True, "plan_id": stored["plan_id"], "checkpoint": stored["sha"],
                    "review_token": prepared["review_token"], "files": stored["files"], "paths": stored["paths"],
                    "resume": prepared["resume"], "recover_owned_index_lock": prepared["stale_lock"],
                    "restore_index": prepared["current"]["index"] != stored["index"]["hash"],
                    "non_reversible": ["Git-ignored files", "remote/API/database side effects"],
                    "attribution": "Execution interval changes; concurrent external edits cannot be attributed by snapshots"}


def apply_undo(ws: str, *, review_token: str, expected_ref: str | None = None) -> dict:
    return _apply_undo(ws, review_token=review_token, expected_ref=expected_ref)


def _apply_undo(ws: str, *, review_token: str, expected_ref=None, _locked=False) -> dict:
    """Apply exactly the reviewed result; never merge again during application."""
    from research_agent.execution import exclusive_request
    try:
        cp._admit_restore(ws)
        with nullcontext() if _locked else exclusive_request(ws, "rollback:" + ws):
            stored, journal = baseline(ws, "selective-undo"), baseline(ws, "checkpoint-rollback")
            if not stored or journal.get("status") == "conflict":
                raise ValueError("Review a conflict-free undo plan first")
            if expected_ref is not None and cp._resolve_owned(ws, expected_ref) != stored["sha"]:
                raise ValueError("Execution changed before applying the chat undo")
            cp._resolve_owned(ws, stored["sha"])
            if journal.get("status") == "complete" and journal.get("plan_id") == stored["plan_id"]:
                raise ValueError("Undo already completed")
            current = cp._state(ws)
            if journal.get("status") in {"applying", "partial"}:
                _resume_plan(ws, stored, current, journal)
            elif current != stored["expected"]:
                raise ValueError("Workspace/index changed after review")
            # Resolved artifacts are also review inputs, not just the business files.
            if stored.get("resolved"):
                artifacts = baseline(ws, "selective-conflicts")
                for path in stored["resolved"]:
                    artifact = next(a for a in artifacts["artifacts"] if a["path"] == path)
                    if hashlib.sha256(Path(artifact["result"]).read_bytes()).hexdigest() != stored["desired"]["files"][path]:
                        raise ValueError("Conflict resolution changed after review")
            prepared = _prepare(ws, stored, current, journal)
            if not review_token or prepared["review_token"] != review_token:
                raise ValueError("Valid undo review token required; review again")
            result = cp._restore_checkpoint(ws, stored["sha"], review_token=review_token, prepared=prepared)
            if result["success"]:
                progress = baseline(ws, "undo-progress")
                baseline(ws, "undo-progress", {"sha": stored["sha"], "paths": sorted(set(progress.get("paths", [])) | set(stored["paths"])),
                                              "index": stored["desired"]["index"]})
            return result
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError, sqlite3.Error) as exc:
        return {"success": False, "reason": str(exc)}


def cancel_conflict(ws: str, *, review_token: str, expected_ref: str | None = None) -> dict:
    """Abandon planning only. An application that has started must be recovered."""
    from research_agent.execution import exclusive_request
    try:
        cp._admit_restore(ws)
        with exclusive_request(ws, "rollback:" + ws):
            journal = baseline(ws, "checkpoint-rollback")
            if expected_ref is not None and cp._resolve_owned(ws, expected_ref) != journal.get("target"):
                raise ValueError("Execution changed before cancelling the chat undo")
            if journal.get("status") != "conflict" or not review_token or journal.get("review_token") != review_token:
                raise ValueError("A valid conflict review token is required")
            baseline(ws, "checkpoint-rollback", {"status": "cancelled", "target": journal["target"]})
            baseline(ws, "selective-undo", {})
            baseline(ws, "selective-conflicts", {})
            return {"success": True, "cancelled": True, "files_changed": False}
    except (RuntimeError, ValueError, OSError, sqlite3.Error) as exc:
        return {"success": False, "reason": str(exc)}
