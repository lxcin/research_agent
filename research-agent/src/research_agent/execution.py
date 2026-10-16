"""Request admission, cooperative cancellation and durable tool attempts."""
from contextlib import contextmanager
from threading import Event, Lock
import hashlib
import json
import os
import sqlite3


class Cancelled(BaseException):
    pass


class RecoveryRequired(BaseException):
    """Stop the loop before uncertain side effects can be worked around."""


def serializable_caps(caps):
    result = {}
    for key, value in caps.items():
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            continue
        result[key] = value
    return result


def restore_execution_state(state, saved):
    """Execution recovery is strict; incompatibility must never enter the loop."""
    try:
        if "fields" in saved:
            state.components.restore_snapshot(saved["fields"], strict=True)
        if "caps" in saved:
            state.caps.update(saved["caps"])
    except Exception as exc:
        raise RecoveryRequired(f"Cannot restore execution state: {exc}") from exc


def checkpoint(run_id, identity, payload=None):
    from research_agent.config import get_data_dir
    db = sqlite3.connect(get_data_dir() / "executions.db", timeout=5)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS checkpoints(run TEXT PRIMARY KEY, identity TEXT, payload TEXT)")
        row = db.execute("SELECT identity,payload FROM checkpoints WHERE run=?", (run_id,)).fetchone()
        if row and row[0] != identity:
            raise RecoveryRequired("Execution identity does not match request/kernel")
        if payload is not None:
            db.execute("INSERT OR REPLACE INTO checkpoints VALUES(?,?,?)", (run_id, identity, json.dumps(payload)))
            db.commit()
        return json.loads(row[1]) if row else None
    finally:
        db.close()


def checkpoint_payload(run_id):
    """Read an existing cursor for snapshot handoff recovery, without creating it."""
    from research_agent.config import get_data_dir
    path = get_data_dir() / "executions.db"
    if not path.exists():
        return None
    db = sqlite3.connect(path, timeout=5)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='checkpoints'").fetchone():
            return None
        row = db.execute("SELECT payload FROM checkpoints WHERE run=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        db.close()


def cancel_checkpoint(run_id, *, only_waiting=False):
    """Persist cancellation atomically; do not overwrite a completed cursor."""
    from research_agent.config import get_data_dir
    path = get_data_dir() / "executions.db"
    if not path.exists():
        return False
    db = sqlite3.connect(path, timeout=5)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='checkpoints'").fetchone():
            return False
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT payload FROM checkpoints WHERE run=?", (run_id,)).fetchone()
        if not row:
            return False
        payload = json.loads(row[0])
        if "final" in payload or (only_waiting and payload.get("interruption", {}).get("status") != "waiting_network"):
            return False
        payload.setdefault("interruption", {})["status"] = "cancelled"
        db.execute("UPDATE checkpoints SET payload=? WHERE run=?", (json.dumps(payload), run_id))
        db.commit()
        return True
    finally:
        db.close()


class CancellationToken:
    def __init__(self):
        self._event = Event()

    def cancel(self):
        self._event.set()

    def check(self):
        if self._event.is_set():
            raise Cancelled()

    def wait(self, seconds):
        import time
        deadline = time.monotonic() + seconds
        self.check()
        while (remaining := deadline - time.monotonic()) > 0:
            self._event.wait(min(remaining, 0.1))
            self.check()


def cancellable_call(invoke, state):
    """Detach local waiting on cancel; retain workspace until transport exits."""
    token = getattr(state, "_cancellation", None)
    if token is None:
        return invoke()
    token.check()
    done = Event()
    outcome = []
    lease = getattr(state, "_request_lease", None)
    release = lease.retain() if lease is not None else lambda: None
    from contextvars import copy_context
    context = copy_context()
    def worker():
        try:
            outcome.append((True, context.run(invoke)))
        except BaseException as exc:
            outcome.append((False, exc))
        finally:
            release()
            done.set()
    import threading
    try:
        threading.Thread(target=worker, daemon=True).start()
    except BaseException:
        release()
        raise
    while not done.wait(0.05):
        token.check()
    token.check()
    success, value = outcome[0]
    if not success:
        raise value
    return value


def quarantine(workspace, reason):
    from research_agent.config import get_data_dir
    root = get_data_dir() / "quarantine"
    root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(os.path.normcase(os.path.realpath(workspace)).encode()).hexdigest()
    (root / key).write_text(reason, encoding="utf-8")


@contextmanager
def exclusive_request(workspace, chat):
    from research_agent.config import get_data_dir
    root = get_data_dir() / "locks"
    root.mkdir(parents=True, exist_ok=True)
    canonical = os.path.normcase(os.path.realpath(workspace or str(get_data_dir() / "workspaces" / "default")))
    marker = get_data_dir() / "quarantine" / hashlib.sha256(canonical.encode()).hexdigest()
    if marker.exists():
        raise RuntimeError("Workspace quarantined: " + marker.read_text(encoding="utf-8"))
    keys = ["workspace:" + os.path.normcase(os.path.realpath(workspace or str(get_data_dir() / "workspaces" / "default"))),
            "chat:" + (chat or "default")]
    handles = []
    try:
        for key in sorted(keys):
            stream = open(root / hashlib.sha256(key.encode()).hexdigest(), "a+b")
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    if not stream.read(1):
                        stream.write(b"0")
                        stream.flush()
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                stream.close()
                raise RuntimeError("Workspace or conversation already has an active request") from None
            handles.append(stream)
        lease = RequestLease(handles)
        try:
            yield lease
        finally:
            lease.release()
            handles = []
    finally:
        if handles:
            RequestLease(handles).release()


class RequestLease:
    def __init__(self, handles):
        self.handles = handles
        self.count = 1
        self.lock = Lock()
        self._finalizers = []

    def before_final_release(self, callback):
        """Run once before the last writer unlocks the workspace."""
        with self.lock:
            if not self.count:
                raise RuntimeError("Request lease already released")
            self._finalizers.append(callback)

    def retain(self):
        with self.lock:
            if not self.count:
                raise RuntimeError("Request lease already released")
            self.count += 1
        return self.release

    def release(self):
        with self.lock:
            self.count -= 1
            if self.count:
                return
            callbacks, self._finalizers = self._finalizers, []
        try:
            for callback in callbacks:
                try:
                    callback()
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception("Workspace finalization failed; snapshot remains unsealed")
        finally:
            for stream in reversed(self.handles):
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                stream.close()


def workspace_digest(snapshot):
    """Compact completion evidence, not a per-tool copy of every file hash."""
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def completed_call(run_id, call_id, name, params):
    """Check replay before approval; dispatch still validates the full contract."""
    return completed_call_result(run_id, call_id, name, params) is not None


def completed_call_result(run_id, call_id, name, params):
    """Return durable completion evidence, or reject an uncertain attempt."""
    from research_agent.config import get_data_dir
    path = get_data_dir() / "executions.db"
    if not path.exists():
        return None
    db = sqlite3.connect(path, timeout=5)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='calls'").fetchone():
            return None
        row = db.execute("SELECT signature,status,result FROM calls WHERE run=? AND call=?", (run_id, call_id)).fetchone()
        if row is None:
            return None
        if json.loads(row[0])[:2] != [name, params]:
            raise RecoveryRequired("Call identity reused with different arguments")
        if row[1] != "completed":
            raise RecoveryRequired("Previous execution outcome unknown; reconciliation required")
        return json.loads(row[2])
    finally:
        db.close()


def execute_once(run_id, call_id, name, params, invoke, state=None, contract_version=None):
    """Never blindly replay an attempt with an unknown external outcome."""
    from research_agent.config import get_data_dir
    from research_agent.tools.schema import ToolResult
    path = get_data_dir() / "executions.db"
    db = sqlite3.connect(path, timeout=5)
    signature = json.dumps([name, params], sort_keys=True, ensure_ascii=False)
    if contract_version is not None:
        signature = json.dumps([name, params, contract_version], sort_keys=True, ensure_ascii=False)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS calls(run TEXT, call TEXT, signature TEXT, status TEXT, result TEXT, PRIMARY KEY(run,call))")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT signature,status,result FROM calls WHERE run=? AND call=?", (run_id, call_id)).fetchone()
        if row:
            db.rollback()
            if row[0] != signature:
                raise RecoveryRequired("Call identity reused with different arguments")
            if row[1] != "completed":
                raise RecoveryRequired("Previous execution outcome unknown; reconciliation required")
            saved = json.loads(row[2])
            if "tool_result" in saved:
                if state is not None:
                    restore_execution_state(state, saved)
                return ToolResult(**saved["tool_result"])
            return ToolResult(**saved)
        db.execute("INSERT INTO calls VALUES(?,?,?,'started',NULL)", (run_id, call_id, signature))
        db.commit()
        result = invoke()
        from dataclasses import asdict
        saved = {"tool_result": asdict(result)}
        if state is not None:
            saved["caps"] = serializable_caps(state.caps)
            saved["fields"] = state.components.snapshot()
            workspace = getattr(state, "workspace_dir", "")
            if workspace:
                from research_agent import checkpoint as cp
                if cp.is_repo(workspace):
                    saved["workspace_digest"] = workspace_digest(cp._state(workspace))
        payload = json.dumps(saved, ensure_ascii=False)
        db.execute("UPDATE calls SET status='completed',result=? WHERE run=? AND call=?", (payload, run_id, call_id))
        db.commit()
        return result
    finally:
        db.close()
