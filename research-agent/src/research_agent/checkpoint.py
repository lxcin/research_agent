"""Latest-turn workspace snapshots and reviewed strict restoration.

Sandboxing (see `research_agent.sandbox`) controls WHERE a command may write; it
mounts the workspace into the container, so writes land on the host directly.
This module answers the other half: *can those writes be undone?*

Before an agent turn runs, `create_checkpoint()` captures the full
workspace tree as a commit object **without touching the working tree or the real
index** (it uses a throwaway index via GIT_INDEX_FILE). The commit is kept
reachable under `refs/research-agent/checkpoints/*`, so `restore_checkpoint()`
can later put the workspace back exactly as it was.

Scope / known gaps:
  - Git-only (the workspace is assumed to be a repo, as with proposal.py). If the
    workspace is not a repo, create_checkpoint() is a no-op.
  - Untracked-but-not-ignored files ARE captured; git-ignored files are NOT.
  - Restore uses reviewed per-file replacements and the original index bytes;
    no blanket clean/reset. External editors still do not form a transaction.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import base64
import hashlib
import stat
import uuid
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

REF_PREFIX = "refs/research-agent/checkpoints"

# Granularity of workspace snapshots:
#   turn    (default) — one checkpoint per agent run, created by the host before
#                       the loop; all file/shell tools in the run share it. Rollback
#                       boundary = "before this request".
#   command (legacy)  — migrated to turn; no per-tool snapshot accumulation.
#   off               — never snapshot.
_VALID_MODES = ("turn", "off")

# ws abspath -> (tree_sha, ref, commit_sha, start_state), for unsealed dedupe.
_LAST: dict[str, tuple] = {}


def _owner(ws):
    return hashlib.sha256(os.path.normcase(os.path.realpath(ws)).encode()).hexdigest()[:12]


def _require(args, ws):
    result = _run(args, ws)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Git snapshot inspection failed")
    return result.stdout


def _index_state(ws):
    gitdir = Path(_require(["rev-parse", "--absolute-git-dir"], ws).strip()).resolve()
    path = Path(_require(["rev-parse", "--path-format=absolute", "--git-path", "index"], ws).strip()).resolve()
    if path != gitdir / "index" or path.is_symlink():
        raise ValueError("Alternate/linked index requires manual handling")
    data = path.read_bytes() if path.exists() else None
    return {"path": str(path), "data": base64.b64encode(data).decode() if data is not None else None,
            "hash": hashlib.sha256(data).hexdigest() if data is not None else None,
            "mode": stat.S_IMODE(path.stat().st_mode) if data is not None else None}


def _lock_state(path):
    if not os.path.lexists(path):
        return None
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise ValueError("Unexpected Git index lock type")
    info = target.stat()
    return {"identity": [info.st_dev, info.st_ino], "hash": hashlib.sha256(target.read_bytes()).hexdigest()}


def _target(ws, relative):
    from research_agent.path_policy import safe_path
    if not relative or Path(relative).is_absolute() or relative.replace("\\", "/").split("/")[0].lower() == ".git":
        raise ValueError("Unsafe checkpoint path")
    path = Path(ws).resolve() / relative
    if safe_path(ws, relative) is None:
        raise ValueError("Checkpoint path escapes workspace")
    for part in (path, *path.parents):
        if part == Path(ws).resolve():
            break
        if os.path.lexists(part) and (part.is_symlink() or getattr(part.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("Linked checkpoint paths require manual handling")
        if part != path and part.exists() and not part.is_dir():
            raise ValueError("Checkpoint parent changed to a file")
    return path


def _state(ws):
    from research_agent.review_baseline import fingerprint
    paths = _require(["ls-files", "-z", "--cached", "--others", "--exclude-standard"], ws)
    files, modes = {}, {}
    for path in set(filter(None, paths.split("\0"))):
        target = _target(ws, path)
        files[path] = fingerprint(ws, path)
        modes[path] = stat.S_IMODE(target.stat().st_mode) if target.is_file() else None
    return {"files": files, "modes": modes, "head": _run(["rev-parse", "HEAD"], ws).stdout.strip(),
            "index": _index_state(ws)["hash"]}


def _resolve_owned(ws, ref):
    prefix = REF_PREFIX + "/" + _owner(ws) + "/"
    refs = _require(["for-each-ref", "--format=%(refname) %(objectname)", prefix], ws)
    candidates = dict(line.split(" ", 1) for line in refs.splitlines())
    if ref in candidates:
        return candidates[ref]
    if ref in candidates.values():
        return ref
    raise ValueError("Unknown checkpoint or checkpoint belongs to another workspace")


def get_checkpoint_mode() -> str:
    """Return turn | off. Legacy 'command' now migrates to turn retention.

    Backward-compatible with the legacy boolean: True -> "turn", False -> "off".
    """
    from research_agent.config import load_config
    raw = (load_config().get("shell") or {}).get("checkpoint", "turn")
    if isinstance(raw, bool):
        return "turn" if raw else "off"
    mode = str(raw).strip().lower()
    return mode if mode in _VALID_MODES else "turn"


def _run(args: list[str], ws: str, env: dict | None = None, timeout: int = 30):
    try:
        return subprocess.run(["git"] + args, cwd=ws, capture_output=True,
                              text=True, errors="replace", timeout=timeout, env=env)
    except Exception as e:  # noqa: BLE001 - caller inspects
        class _R:
            returncode, stdout, stderr = -1, "", str(e)
        return _R()


def is_repo(ws: str) -> bool:
    return bool(ws) and os.path.exists(os.path.join(ws, ".git"))


def _has_head(ws: str) -> bool:
    return _run(["rev-parse", "--verify", "HEAD"], ws).returncode == 0


def checkpoint_enabled() -> bool:
    return get_checkpoint_mode() != "off"


def create_checkpoint(ws: str, label: str = "", dedupe: bool = True) -> dict:
    """Snapshot the workspace tree into a checkpoint ref. Never raises.

    Returns {"success", "ref", "sha", "reason"}. With ``dedupe`` (default), if the
    workspace tree is identical to the last checkpoint for this workspace, no new
    commit/ref is created and the previous ref is returned with ``skipped=True``.
    """
    if not is_repo(ws):
        return {"success": False, "reason": "workspace is not a git repo"}
    try:
        start = _state(ws)
        index_before = _index_state(ws)
        if start["index"] != index_before["hash"]:
            raise ValueError("Index changed during snapshot preparation")
    except (OSError, ValueError, RuntimeError) as exc:
        return {"success": False, "reason": str(exc)}
    from research_agent.tools.git_tool import _ensure_git_config
    _ensure_git_config(ws)

    fd, idx = tempfile.mkstemp(prefix="ra-index-")
    os.close(fd)
    os.remove(idx)  # git wants to create it itself
    env = dict(os.environ, GIT_INDEX_FILE=idx)
    try:
        read = _run(["read-tree", "--empty"], ws, env)
        if read.returncode:
            raise RuntimeError(read.stderr.strip() or "read-tree failed")
        # Store original bytes, independent of clean/smudge filters and CRLF.
        for path, digest in sorted(start["files"].items()):
            if digest is None:
                continue
            data = _target(ws, path).read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise RuntimeError("File changed during snapshot: " + path)
            blob = subprocess.run(["git", "hash-object", "-w", "--stdin", "--no-filters"],
                                  cwd=ws, input=data, capture_output=True, timeout=30)
            if blob.returncode:
                raise RuntimeError("Could not store snapshot content")
            mode = "100755" if start["modes"][path] & 0o111 else "100644"
            entry = _run(["update-index", "--add", "--cacheinfo", mode, blob.stdout.decode().strip(), path], ws, env)
            if entry.returncode:
                raise RuntimeError(entry.stderr.strip() or "Could not store snapshot entry")
        if _state(ws) != start:
            raise RuntimeError("Workspace/index changed during snapshot")
        tree = _run(["write-tree"], ws, env).stdout.strip()
        if not tree:
            return {"success": False, "reason": "no tree produced"}

        key = os.path.abspath(ws)
        if dedupe:
            prev = _LAST.get(key)
            from research_agent.review_baseline import baseline
            if (prev and prev[0] == tree and len(prev) == 4 and prev[3] == start
                    and not baseline(ws, "checkpoint:" + prev[2])
                    and _run(["rev-parse", "--verify", prev[1]], ws).stdout.strip() == prev[2]):
                return {"success": True, "ref": prev[1], "sha": prev[2],
                        "skipped": True}

        msg = f"checkpoint {label} [{uuid.uuid4().hex}]"
        args = ["commit-tree", tree, "-m", msg]
        if _has_head(ws):
            args += ["-p", "HEAD"]
        c = _run(args, ws, env)
        if c.returncode != 0:
            return {"success": False, "reason": c.stderr.strip() or "commit-tree failed"}
        sha = c.stdout.strip()

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        owner = _owner(ws)
        ref = f"{REF_PREFIX}/{owner}/{ts}-{uuid.uuid4().hex[:8]}"
        published = _run(["update-ref", ref, sha], ws)
        if published.returncode:
            return {"success": False, "reason": published.stderr.strip() or "checkpoint reference publication failed"}
        from research_agent.review_baseline import baseline
        index_ref = ""
        if index_before["data"] is not None:
            # A staged blob may differ from both HEAD and the working file. Keep
            # its tree reachable too; SQLite's saved index is not a Git GC root.
            Path(idx).write_bytes(base64.b64decode(index_before["data"]))
            staged = _run(["write-tree"], ws, env)
            if staged.returncode:
                raise RuntimeError("Cannot preserve staged objects; unresolved/special index requires manual handling")
            index_ref = "refs/research-agent/indexes/" + owner + "/" + sha
            _require(["update-ref", index_ref, staged.stdout.strip()], ws)
        baseline(ws, "checkpoint-start:" + sha, {"version": 2, "ref": ref, "state": start,
                                                 "index": index_before, "index_ref": index_ref})
        _LAST[key] = (tree, ref, sha, start)
        return {"success": True, "ref": ref, "sha": sha}
    except Exception as exc:
        return {"success": False, "reason": "checkpoint creation failed: " + str(exc)}
    finally:
        try:
            os.remove(idx)
        except OSError:
            pass


def snapshot_for_run(state, ws: str, label: str = "run", *, on_created=None) -> str:
    """Snapshot at execution admission, under the existing workspace lease."""
    from contextlib import nullcontext
    from research_agent.execution import exclusive_request
    if not is_repo(ws):
        state._last_checkpoint = ""
        return ""
    lease = getattr(state, "_request_lease", None)
    with nullcontext() if lease is not None else exclusive_request(ws, "snapshot:" + _owner(ws)):
        return _snapshot_for_run(state, ws, label, on_created=on_created)


def _snapshot_for_run(state, ws: str, label: str, *, on_created=None) -> str:
    """Create the turn-level checkpoint before a run (mode='turn').

    Stores the ref on ``state._last_checkpoint`` so shell_exec can report it.
    No-op when disabled or outside Git. In a Git workspace, failure stops the run.
    Save the new actual baseline before retiring references from the previous turn.
    """
    try:
        state._last_checkpoint = ""
        if not is_repo(ws):
            return ""
        from research_agent.review_baseline import baseline
        from research_agent.execution import RecoveryRequired
        journal = baseline(ws, "checkpoint-rollback")
        if journal.get("status") in {"applying", "partial"}:
            raise RecoveryRequired("Resolve or resume the pending rollback before starting a new turn")
        from research_agent.tools import get_registry
        if get_registry().background_tasks.workspace_pending(ws):
            raise RecoveryRequired("Background workspace writes must be drained or reconciled before starting a new turn")
        recover_snapshot_transition(ws)
        from research_agent.checkpoint_events import consume_workspace
        consume_workspace(ws, locked=True)
        if get_checkpoint_mode() == "off":
            if on_created is not None:
                on_created("")
            # Opting out must not present a previous turn as this execution's proposal.
            for key in ("active-turn", "proposal", "proposal_meta", "proposal_head"):
                baseline(ws, key, {})
            return ""
        r = create_checkpoint(ws, label=label, dedupe=False)
        if not r.get("success"):
            raise RecoveryRequired("Could not preserve the new turn baseline: " + r.get("reason", ""))
        ref = r.get("ref", "") if r.get("success") else ""
        if ref:
            active = {"ref": ref, "sha": r["sha"],
                      "execution_id": getattr(state, "_execution_id", ""),
                      "chat_id": getattr(state, "active_chat_id", "")}
            if on_created is not None:
                # Pin S and its handoff intent before updating the execution
                # cursor. Old evidence stays reachable until that write commits.
                baseline(ws, "snapshot-transition", {"previous": baseline(ws, "active-turn"),
                         "next": active, "preserve_conflicts": journal.get("status") == "conflict"})
                on_created(ref)
            try:
                state._last_checkpoint = ref
            except Exception:
                pass
            baseline(ws, "active-turn", active)
            from research_agent.checkpoint_events import register_run
            register_run(state, ws, ref, r["sha"])
            _finish_snapshot_transition(ws, active, journal.get("status") == "conflict")
        return ref
    except Exception as exc:
        from research_agent.execution import RecoveryRequired
        raise RecoveryRequired("Turn snapshot preparation failed: " + str(exc)) from exc


def _finish_snapshot_transition(ws, active, preserve_conflicts):
    from research_agent.review_baseline import baseline
    from research_agent.checkpoint_events import retire_previous
    _retire_previous_turns(ws, active["ref"])
    retire_previous(ws, active["sha"])
    _cleanup_undo_artifacts(ws, preserve=preserve_conflicts)
    for key in ("proposal", "proposal_meta", "proposal_head", "checkpoint-rollback", "selective-undo", "selective-conflicts", "undo-progress"):
        baseline(ws, key, {})
    baseline(ws, "snapshot-transition", {})


def recover_snapshot_transition(ws):
    """Replay/abandon a durable handoff while the workspace lease is held."""
    from research_agent.review_baseline import baseline
    from research_agent.execution import checkpoint_payload, RecoveryRequired
    intent = baseline(ws, "snapshot-transition")
    if not intent:
        return
    if baseline(ws, "checkpoint-rollback").get("status") in {"applying", "partial"}:
        raise RecoveryRequired("Complete the partial rollback before recovering snapshot handoff")
    active, target = baseline(ws, "active-turn"), intent["next"]
    if active not in (intent["previous"], target):
        raise RecoveryRequired("Snapshot handoff no longer owns the active workspace")
    saved = checkpoint_payload(target["execution_id"])
    if not saved or saved.get("workspace_checkpoint") != target["ref"]:
        if active == target:
            raise RecoveryRequired("Published snapshot has no matching execution cursor")
        # Cursor was never committed: keep old S and evidence. The unused new
        # ref remains pinned until the next successful rollover retires it.
        baseline(ws, "snapshot-transition", {})
        return
    if _resolve_owned(ws, target["ref"]) != target["sha"]:
        raise RecoveryRequired("Snapshot handoff target changed")
    baseline(ws, "active-turn", target)
    _finish_snapshot_transition(ws, target, intent.get("preserve_conflicts", False))


def _retire_previous_turns(ws, keep_ref):
    """Release our references only; Git object reclamation is left to maintenance."""
    from research_agent.review_baseline import baseline, delete_baselines
    owner = _owner(ws)
    refs = _require(["for-each-ref", "--format=%(refname) %(objectname)",
                     REF_PREFIX + "/" + owner + "/", "refs/research-agent/ends/" + owner + "/",
                     "refs/research-agent/undo/" + owner + "/", "refs/research-agent/indexes/" + owner + "/"], ws)
    active = baseline(ws, "active-turn")
    retired = False
    for line in refs.splitlines():
        ref, sha = line.split(" ", 1)
        # The index ref is keyed by checkpoint SHA, not the human-readable ref suffix.
        if ref == keep_ref or ref == "refs/research-agent/indexes/" + owner + "/" + active.get("sha", ""):
            continue
        result = _run(["update-ref", "-d", ref, sha], ws)
        if result.returncode:
            raise RuntimeError("Could not retire previous snapshot reference")
        retired = True
        delete_baselines(ws, ["checkpoint-start:" + sha, "checkpoint:" + sha])
        # End evidence is embedded in its start record; its dedicated ref is in the enumeration.
    if retired:
        from research_agent.git_maintenance import enqueue
        enqueue(ws)  # Durable background request; GC never runs in this call.


def _cleanup_undo_artifacts(ws, *, preserve=False):
    from research_agent.config import get_data_dir
    from research_agent.review_baseline import baseline
    root = get_data_dir().resolve() / "undo-artifacts" / _owner(ws)
    owned = baseline(ws, "undo-artifact-files")
    # A superseded conflict may contain a manually edited resolution. Retain its
    # directory independently of the expired plan, including on later rollovers.
    retained = set(owned.get("retained", []))
    if preserve:
        retained.update(owned.get("directories", []))
    remaining = []
    for value in owned.get("directories", []):
        directory = Path(value)
        if value in retained:
            if directory.exists():
                remaining.append(value)
            continue
        if directory.is_symlink() or not directory.resolve().is_relative_to(root) or directory.parent != root:
            remaining.append(value)
            continue
        try:
            for name in ("before", "agent-end", "current", "result"):
                path = directory / name
                if path.is_symlink():
                    raise ValueError("Linked conflict artifact retained")
                path.unlink(missing_ok=True)
            if directory.exists():
                directory.rmdir()  # Never recursively delete unexpected files.
        except (OSError, ValueError):
            remaining.append(value)
    baseline(ws, "undo-artifact-files", {"directories": remaining,
                                        "retained": sorted(retained.intersection(remaining))})


def list_checkpoints(ws: str, limit: int = 20) -> list[dict]:
    if not is_repo(ws):
        return []
    r = _run(["for-each-ref", "--sort=-refname",
              "--format=%(refname)\t%(objectname:short)\t%(subject)",
              REF_PREFIX + "/" + _owner(ws) + "/"], ws)
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            out.append({"ref": parts[0], "sha": parts[1],
                        "label": parts[2] if len(parts) > 2 else ""})
    return out[:limit]


def seal_checkpoint(ws: str, ref: str) -> dict:
    """Seal once. A refresh never approves a later human edit."""
    from research_agent.review_baseline import baseline, baseline_once
    try:
        sha = _resolve_owned(ws, ref)
        if not baseline(ws, "checkpoint-start:" + sha):
            return {"success": False, "reason": "checkpoint start metadata missing"}
        state = _state(ws)
        from research_agent.review_baseline import baseline
        existing = baseline(ws, "checkpoint:" + sha)
        if existing:
            same = all(existing.get(key) == value for key, value in state.items())
            return {"success": same, "reason": "" if same else "checkpoint already sealed; baseline remains unchanged"}
        evidence = capture_checkpoint_end(ws, ref)
        return publish_checkpoint_end(ws, ref, evidence)
    except (RuntimeError, ValueError, OSError, sqlite3.Error) as exc:
        return {"success": False, "reason": str(exc)}


def capture_checkpoint_end(ws, ref):
    """Capture under a writer lease. Leave its Git refs pinned until publication."""
    from research_agent.review_baseline import baseline
    sha = _resolve_owned(ws, ref)
    if not baseline(ws, "checkpoint-start:" + sha):
        raise ValueError("Checkpoint start evidence missing")
    state = _state(ws)
    end = create_checkpoint(ws, label="sealed-end", dedupe=False)
    if not end.get("success"):
        raise RuntimeError("Could not preserve complete end snapshot")
    end_start = baseline(ws, "checkpoint-start:" + end["sha"])
    if end_start["state"] != state or _state(ws) != state:
        raise RuntimeError("Workspace changed while capturing end snapshot")
    end_ref = "refs/research-agent/ends/" + _owner(ws) + "/" + sha
    _require(["update-ref", end_ref, end["sha"]], ws)
    state.update(snapshot_sha=end["sha"], snapshot_ref=end_ref, index_data=end_start["index"], source_ref=end["ref"])
    return state


def publish_checkpoint_end(ws, ref, evidence):
    """Publish captured evidence; never infer an end version from today's files."""
    from research_agent.review_baseline import baseline, baseline_once, delete_baselines
    sha = _resolve_owned(ws, ref)
    if not baseline(ws, "checkpoint-start:" + sha):
        raise ValueError("Checkpoint start evidence missing")
    expected_ref = "refs/research-agent/ends/" + _owner(ws) + "/" + sha
    if evidence.get("snapshot_ref") != expected_ref or _require(["rev-parse", "--verify", expected_ref], ws).strip() != evidence.get("snapshot_sha"):
        raise ValueError("Captured end reference changed")
    index = evidence.get("index_data", {})
    if index.get("path") != baseline(ws, "checkpoint-start:" + sha)["index"]["path"]:
        raise ValueError("Captured index belongs to another worktree")
    source_ref = evidence.get("source_ref")
    if source_ref and not source_ref.startswith(REF_PREFIX + "/" + _owner(ws) + "/"):
        raise ValueError("Captured source reference belongs to another worktree")
    existing = baseline(ws, "checkpoint:" + sha)
    if not existing:
        source = baseline(ws, "checkpoint-start:" + evidence["snapshot_sha"])
        if (not source or source.get("ref") != source_ref or source["index"] != index
                or any(evidence.get(key) != value for key, value in source["state"].items())):
            raise ValueError("Captured evidence does not match its immutable snapshot metadata")
    stored = baseline_once(ws, "checkpoint:" + sha, evidence)
    same = stored == evidence
    if same:
        if source_ref:
            source = _run(["rev-parse", "--verify", source_ref], ws)
            if not source.returncode:
                if source.stdout.strip() != evidence["snapshot_sha"]:
                    raise ValueError("Captured source ref changed before cleanup")
                _require(["update-ref", "-d", source_ref, evidence["snapshot_sha"]], ws)
        delete_baselines(ws, ["checkpoint-start:" + evidence["snapshot_sha"]])
    return {"success": same, "reason": "" if same else "Checkpoint already sealed; evidence remains unchanged"}


def seal_checkpoint_if_idle(ws, ref):
    """Do not approve a moving workspace while background writes are pending."""
    try:
        from research_agent.tools import get_registry
        if get_registry().background_tasks.workspace_pending(ws):
            return {"success": False, "reason": "background workspace writes are not drained"}
        return seal_checkpoint(ws, ref)
    except Exception as exc:
        return {"success": False, "reason": str(exc)}


def _restore_plan(ws, ref):
    from research_agent.review_baseline import baseline
    sha = _resolve_owned(ws, ref)
    start = baseline(ws, "checkpoint-start:" + sha)
    end = baseline(ws, "checkpoint:" + sha)
    if not start or start.get("version") not in (1, 2) or not end:
        raise ValueError("checkpoint requires start metadata and a sealed post-agent baseline")
    current = _state(ws)
    journal = baseline(ws, "checkpoint-rollback")
    if journal.get("kind") == "selective" and journal.get("status") in {"applying", "partial", "conflict"}:
        return {"success": False, "reason": "Use selective undo review to resolve the pending operation"}
    if journal.get("target") == sha and journal.get("status") == "complete":
        return {"success": False, "reason": "checkpoint already restored", "backup": journal.get("backup")}
    resume = journal.get("target") == sha and journal.get("status") in ("applying", "partial")
    before = start["state"]
    conflicts = []
    for path in current["files"].keys() | end["files"].keys() | before["files"].keys():
        actual = (current["files"].get(path), current["modes"].get(path))
        expected = (end["files"].get(path), end["modes"].get(path))
        original = (before["files"].get(path), before["modes"].get(path))
        if actual != expected and not (resume and actual == original):
            conflicts.append(path)
    allowed_indices = {end["index"]} | ({before["index"]} if resume else set())
    if conflicts or current["head"] != end["head"] or current["index"] not in allowed_indices:
        return {"success": False, "reason": "workspace or index changed after agent execution", "conflicts": sorted(conflicts),
                "index_conflict": current["index"] not in allowed_indices, "backup": journal.get("backup") if resume else None}
    index = _index_state(ws)
    if index["path"] != start["index"]["path"]:
        raise ValueError("Checkpoint index belongs to another worktree")
    lock_state = _lock_state(index["path"] + ".lock")
    stale_lock = False
    if lock_state is not None:
        recorded = journal.get("index_lock", {})
        stale_lock = (resume and recorded.get("identity") == lock_state["identity"]
                      and lock_state["hash"] in {recorded.get("marker_hash"), start["index"]["hash"]})
        if not stale_lock:
            return {"success": False, "reason": "Git index lock requires explicit manual inspection"}
    changes = []
    for path in sorted(before["files"].keys() | end["files"].keys()):
        target = _target(ws, path)
        desired = before["files"].get(path)
        mode = before["modes"].get(path)
        from research_agent.review_baseline import fingerprint
        # Check target paths too: an ignored file must not be silently replaced.
        if fingerprint(ws, path) != current["files"].get(path):
            return {"success": False, "reason": "ignored or external file occupies rollback path", "conflicts": [path]}
        if desired == current["files"].get(path) and mode == current["modes"].get(path):
            continue
        data = None
        if desired is not None:
            result = subprocess.run(["git", "cat-file", "blob" if start.get("version") == 2 else "--filters", sha + ":" + path], cwd=ws,
                                    capture_output=True, timeout=30)
            if result.returncode or hashlib.sha256(result.stdout).hexdigest() != desired:
                return {"success": False, "reason": "snapshot content/filter no longer matches original bytes", "conflicts": [path]}
            data = result.stdout
        changes.append({"path": path, "data": data, "mode": mode})
    token = hashlib.sha256(json.dumps({"sha": sha, "start": start, "current": current, "index_lock": lock_state,
        "resume_attempt": journal.get("attempt_backup") if resume else None}, sort_keys=True).encode()).hexdigest()
    return {"success": True, "sha": sha, "start": start, "current": current, "changes": changes,
            "review_token": token, "resume": resume, "index_lock": lock_state, "stale_lock": stale_lock,
            "original_backup": journal.get("backup") if resume else None}


def _admit_restore(ws):
    from research_agent.docker_ownership import reconcile_before_request
    from research_agent.background_tasks import recover_workspace
    reconcile_before_request()
    recover_workspace(ws)


def review_checkpoint(ws: str, ref: str) -> dict:
    """Produce a concrete plan without changing files, index or review evidence."""
    from research_agent.execution import exclusive_request
    try:
        _admit_restore(ws)
        with exclusive_request(ws, "rollback:" + ws):
            plan = _restore_plan(ws, ref)
            if not plan["success"]:
                return plan
            import difflib
            files = []
            for change in plan["changes"]:
                target = _target(ws, change["path"])
                existing = target.read_bytes() if target.exists() else b""
                desired = change["data"] or b""
                diff = ""
                if b"\0" not in existing + desired and max(len(existing), len(desired)) <= 64000:
                    diff = "".join(difflib.unified_diff(existing.decode('utf-8', 'replace').splitlines(True),
                        desired.decode('utf-8', 'replace').splitlines(True), fromfile='current/' + change['path'],
                        tofile='checkpoint/' + change['path']))
                files.append({"path": change["path"], "action": "delete" if change["data"] is None else "restore",
                              "diff": diff[:8000], "diff_truncated": len(diff) > 8000,
                              "preview_unavailable": b"\0" in existing + desired or max(len(existing), len(desired)) > 64000,
                              "target_hash": plan["start"]["state"]["files"].get(change["path"])})
            return {"success": True, "checkpoint": plan["sha"], "review_token": plan["review_token"],
                    "files": files, "restore_index": plan["current"]["index"] != plan["start"]["index"]["hash"],
                    "current_index_hash": plan["current"]["index"], "target_index_hash": plan["start"]["index"]["hash"],
                    "head": plan["current"]["head"], "head_will_change": False,
                    "resume": plan["resume"], "backup": plan["original_backup"],
                    "recover_owned_index_lock": plan["stale_lock"],
                    "non_reversible": ["Git-ignored files", "remote/API/database side effects"]}
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError, sqlite3.Error) as exc:
        return {"success": False, "reason": str(exc)}


def restore_checkpoint(ws: str, ref: str, *, review_token=None) -> dict:
    from research_agent.execution import exclusive_request
    try:
        _admit_restore(ws)
        with exclusive_request(ws, "rollback:" + ws):
            return _restore_checkpoint(ws, ref, review_token=review_token)
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError, sqlite3.Error) as exc:
        return {"success": False, "reason": str(exc)}


def _restore_checkpoint(ws, ref, *, review_token=None, prepared=None):
    from research_agent.review_baseline import baseline, fingerprint
    plan = prepared if prepared is not None else _restore_plan(ws, ref)
    if not plan["success"]:
        return plan
    if not review_token or plan["review_token"] != review_token:
        return {"success": False, "reason": "valid rollback review token required; inspect the plan again"}
    backup = create_checkpoint(ws, label="before-rollback", dedupe=False)
    if not backup.get("success"):
        return {"success": False, "reason": "could not preserve rollback backup"}
    journal = {"status": "applying", "target": plan["sha"], "backup": plan["original_backup"] or backup["ref"],
               "attempt_backup": backup["ref"], "review_token": review_token, "applied": [],
               "kind": plan.get("kind", "snapshot"), "plan_id": plan.get("plan_id")}
    lock = Path(plan["start"]["index"]["path"] + ".lock")
    descriptor = None
    lock_owned = False
    lock_identity = None
    expected_files = dict(plan["current"]["files"])
    try:
        if plan["stale_lock"]:
            if _lock_state(lock) != plan["index_lock"]:
                raise RuntimeError("Orphan index lock changed after review")
            lock.unlink()
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        lock_owned = True
        lock_info = os.fstat(descriptor)
        lock_identity = [lock_info.st_dev, lock_info.st_ino]
        marker = ("RESEARCH_AGENT_ROLLBACK:" + uuid.uuid4().hex).encode()
        if os.write(descriptor, marker) != len(marker):
            raise OSError("Could not mark rollback index lock")
        os.fsync(descriptor)
        info = lock.stat()
        journal["index_lock"] = {"identity": [info.st_dev, info.st_ino], "marker_hash": hashlib.sha256(marker).hexdigest()}
        if _state(ws) != plan["current"]:
            raise RuntimeError("Workspace/index changed before rollback")
        baseline(ws, "checkpoint-rollback", journal)
        for change in plan["changes"]:
            path = change["path"]
            target = _target(ws, path)
            if fingerprint(ws, path) != expected_files.get(path):
                raise RuntimeError("File changed before rollback write: " + path)
            if change["data"] is None:
                if target.exists():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".rollback-")
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(change["data"])
                        stream.flush()
                        os.fsync(stream.fileno())
                    if change["mode"] is not None:
                        os.chmod(temporary, change["mode"])
                    if fingerprint(ws, path) != expected_files.get(path):
                        raise RuntimeError("File changed during rollback preparation: " + path)
                    os.replace(temporary, target)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            desired = plan["start"]["state"]["files"].get(path)
            if fingerprint(ws, path) != desired:
                raise RuntimeError("File changed after rollback write: " + path)
            expected_files[path] = desired
            journal["applied"].append(path)
            baseline(ws, "checkpoint-rollback", journal)
        if _index_state(ws)["hash"] != plan["current"]["index"]:
            raise RuntimeError("Index changed before replacement")
        original = plan["start"]["index"]
        if original["data"] is None:
            Path(original["path"]).unlink(missing_ok=True)
        else:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.ftruncate(descriptor, 0)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(base64.b64decode(original["data"]))
                stream.flush()
                os.fsync(stream.fileno())
            if original["mode"] is not None:
                os.chmod(lock, original["mode"])
            if _lock_state(lock) != {"identity": lock_identity, "hash": original["hash"]}:
                raise RuntimeError("Prepared index lock changed before replacement")
            os.replace(lock, original["path"])
            lock_owned = False
        final = _state(ws)
        for path in final["files"].keys() | plan["start"]["state"]["files"].keys():
            if (final["files"].get(path) != plan["start"]["state"]["files"].get(path)
                    or final["modes"].get(path) != plan["start"]["state"]["modes"].get(path)):
                raise RuntimeError("Workspace changed during rollback verification: " + path)
        if final["index"] != original["hash"] or final["head"] != plan["current"]["head"]:
            raise RuntimeError("Index or HEAD changed during rollback verification")
        journal["status"] = "complete"
        baseline(ws, "checkpoint-rollback", journal)
        return {"success": True, "restored": plan["sha"], "backup": journal["backup"], "attempt_backup": backup["ref"]}
    except Exception as exc:
        journal.update(status="partial", error=str(exc))
        baseline(ws, "checkpoint-rollback", journal)
        return {"success": False, "reason": str(exc), "backup": journal["backup"], "attempt_backup": backup["ref"],
                "partial": bool(journal["applied"]), "requires_review": True}
    finally:
        if descriptor is not None:
            os.close(descriptor)
        # Only remove our own index lock, never a lock that blocked admission.
        if lock_owned and os.path.lexists(lock):
            info = lock.lstat()
            if [info.st_dev, info.st_ino] == lock_identity:
                lock.unlink()
