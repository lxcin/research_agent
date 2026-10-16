"""Host-owned background command lifecycle and cross-process control records."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import os
import hashlib
from pathlib import Path
import sqlite3
from threading import RLock, Thread
import time
import uuid

from research_agent.execution import CancellationToken, Cancelled, RecoveryRequired, exclusive_request, quarantine

ACTIVE = ("running", "cancelling")


def _now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _db(path, initialize=False):
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        if initialize:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS tasks(
                id TEXT PRIMARY KEY, chat TEXT NOT NULL, execution TEXT NOT NULL,
                workspace TEXT NOT NULL, command TEXT NOT NULL, backend TEXT NOT NULL,
                started TEXT NOT NULL, finished TEXT, status TEXT NOT NULL,
                returncode INTEGER, error TEXT, output TEXT, checkpoint TEXT)""")
            db.execute("CREATE INDEX IF NOT EXISTS tasks_chat ON tasks(chat, started)")
            db.execute("CREATE INDEX IF NOT EXISTS tasks_execution ON tasks(execution)")
            db.execute("CREATE TABLE IF NOT EXISTS task_sessions(chat TEXT PRIMARY KEY,closed INTEGER NOT NULL)")
            from research_agent.checkpoint_events import initialize as initialize_snapshot_events
            initialize_snapshot_events(db)
        yield db
        db.commit()
    finally:
        db.close()


def _database():
    from research_agent.config import get_data_dir
    return get_data_dir() / "background_tasks.db"


def _owner_lease(path, task_id):
    return exclusive_request(str(path.parent / "background_jobs" / task_id), "background:" + task_id)


class TaskCancellation(CancellationToken):
    def __init__(self, path, task_id, parent):
        super().__init__()
        self.path, self.task_id, self.parent = path, task_id, parent

    def check(self):
        super().check()
        if self.parent is not None:
            self.parent.check()
        with _db(self.path) as db:
            row = db.execute("SELECT status FROM tasks WHERE id=?", (self.task_id,)).fetchone()
        if row is None or row["status"] not in ACTIVE:
            raise RecoveryRequired("Background task control record is missing or incompatible")
        if row["status"] == "cancelling":
            raise Cancelled()


class SessionCancellation(CancellationToken):
    def __init__(self, path, chat, parent):
        super().__init__()
        self.path, self.chat, self.parent = path, chat, parent

    def check(self):
        super().check()
        self.parent.check()
        with _db(self.path) as db:
            row = db.execute("SELECT closed FROM task_sessions WHERE chat=?", (self.chat,)).fetchone()
        if row is not None and row["closed"]:
            raise Cancelled()

    def cancel(self):
        super().cancel()
        self.parent.cancel()


@dataclass
class RunningTask:
    id: str
    chat: str
    execution: str
    path: Path
    token: TaskCancellation
    thread: Thread | None = None


class BackgroundTasks:
    def __init__(self):
        self._lock = RLock()
        self._jobs = {}
        self._accepting = True
        self._closed_sessions = set()
        self._registered_exit = False
        self._failures = {}

    def resume(self):
        with self._lock:
            if self._jobs or self._failures:
                raise RuntimeError("Background tasks must drain before reactivation")
            self._accepting = True
            if not self._registered_exit:
                import atexit
                atexit.register(self.shutdown)
                self._registered_exit = True

    def reopen_session(self, chat):
        self.wait_session_idle(chat, timeout=0)
        with _db(_database(), initialize=True) as db:
            db.execute("DELETE FROM task_sessions WHERE chat=?", (chat,))
        with self._lock:
            self._closed_sessions.discard(chat)

    def session_token(self, chat, parent):
        path = _database()
        with _db(path, initialize=True) as db:
            row = db.execute("SELECT closed FROM task_sessions WHERE chat=?", (chat,)).fetchone()
        if row is not None and row["closed"]:
            raise RuntimeError("Session is closed; explicitly reopen it before a new request")
        with self._lock:
            self._closed_sessions.discard(chat)
        return SessionCancellation(path, chat, parent)

    def start(self, *, chat, execution, workspace, command, backend, invoke, leases,
              parent=None, checkpoint="", request_lease=None):
        """Take ownership of acquired leases; release only after terminal state."""
        path = _database()
        chat = chat or "default"
        task_id = uuid.uuid4().hex
        execution = execution or "adhoc-" + uuid.uuid4().hex
        owner = _owner_lease(path, task_id)
        owner_entered = False
        guard_release = None
        try:
            if request_lease is not None:
                guard_release = request_lease.retain()
            with self._lock:
                if not self._accepting or chat in self._closed_sessions:
                    raise RuntimeError("Background task admission is closed")
                owner.__enter__()
                owner_entered = True
                with _db(path, initialize=True) as db:
                    # The read and insert share a write transaction, so a
                    # different process cannot close admission between them.
                    db.execute("BEGIN IMMEDIATE")
                    session = db.execute("SELECT closed FROM task_sessions WHERE chat=?", (chat,)).fetchone()
                    if session is not None and session["closed"]:
                        raise RuntimeError("Background task admission is closed")
                    db.execute("INSERT INTO tasks(id,chat,execution,workspace,command,backend,started,status,checkpoint) VALUES(?,?,?,?,?,?,?,'running',?)",
                               (task_id, chat, execution, os.path.normcase(os.path.realpath(workspace)), command, backend, _now(), checkpoint))
                job = RunningTask(task_id, chat, execution, path, TaskCancellation(path, task_id, parent))
                self._jobs[task_id] = job

                def run():
                    status, code, error, output = "error", None, "", ""
                    try:
                        job.token.check()
                        result = invoke(job.token)
                        code = result.get("returncode")
                        status = "done" if result.get("success") else "timeout" if result.get("error") == "timeout" else "failed"
                        error = result.get("error") or ""
                        output = "STDOUT:\n" + (result.get("stdout") or "")[:4000] + "\nSTDERR:\n" + (result.get("stderr") or "")[:2000]
                    except Cancelled:
                        status = "cancelled"
                    except RecoveryRequired as exc:
                        status, error = "recovery_required", str(exc)
                    except Exception as exc:
                        import subprocess
                        status = "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "error"
                        error = str(exc)
                    except BaseException as exc:
                        status, error = "recovery_required", "Background worker interrupted: " + str(exc)
                        quarantine(workspace, error)
                    finally:
                        try:
                            leases.close()
                        except Exception as exc:
                            status, error = "recovery_required", "Task lease cleanup failed: " + str(exc)
                            quarantine(workspace, error)
                        try:
                            with _db(path) as db:
                                db.execute("UPDATE tasks SET status=?,finished=?,returncode=?,error=?,output=? WHERE id=?",
                                           (status, _now(), code, error, output, task_id))
                                from research_agent.checkpoint_events import task_finished
                                task_finished(db, task_id, execution, checkpoint, status)
                        except Exception as exc:
                            quarantine(workspace, "Background completion record failed: " + str(exc))
                            status, error = "recovery_required", str(exc)
                        finally:
                            try:
                                owner.__exit__(None, None, None)
                            finally:
                                try:
                                    if guard_release is not None:
                                        guard_release()
                                finally:
                                    with self._lock:
                                        if status == "recovery_required":
                                            self._failures[task_id] = error
                                        self._jobs.pop(task_id, None)

                job.thread = Thread(target=run, name="background-" + task_id[:8], daemon=True)
                try:
                    job.thread.start()
                except BaseException:
                    self._jobs.pop(task_id, None)
                    with _db(path) as db:
                        db.execute("UPDATE tasks SET status='error',finished=?,error='Worker startup failed' WHERE id=?", (_now(), task_id))
                        from research_agent.checkpoint_events import task_finished
                        task_finished(db, task_id, execution, checkpoint, "error")
                    raise
            return {"task_id": task_id, "execution_id": execution, "status": "started"}
        except BaseException:
            if owner_entered:
                owner.__exit__(None, None, None)
            try:
                leases.close()
            finally:
                if guard_release is not None:
                    guard_release()
            raise

    def list(self, chat, task_id=""):
        path = _database()
        with _db(path, initialize=True) as db:
            rows = db.execute("SELECT * FROM tasks WHERE chat=? AND (?='' OR id=?) ORDER BY CASE WHEN status IN ('running','cancelling','recovery_required') THEN 0 ELSE 1 END, started DESC LIMIT 100",
                              (chat or "default", task_id, task_id)).fetchall()
        result = []
        for row in rows:
            record = dict(row)
            if record["status"] in ACTIVE:
                owner = _owner_lease(path, record["id"])
                try:
                    owner.__enter__()
                except RuntimeError:
                    pass  # a live owner still holds the task lease
                else:
                    try:
                        with _db(path) as db:
                            changed = db.execute("UPDATE tasks SET status='recovery_required',finished=?,error='Task owner exited; outcome requires reconciliation' WHERE id=? AND status IN ('running','cancelling')",
                                                 (_now(), record["id"])).rowcount
                            if changed:
                                quarantine(record["workspace"], "Background task owner exited: " + record["id"])
                            record = dict(db.execute("SELECT * FROM tasks WHERE id=?", (record["id"],)).fetchone())
                    finally:
                        owner.__exit__(None, None, None)
            result.append(record)
        return result

    def cancel(self, *, chat=None, task_id=None, execution=None):
        """Persist a cancellation request, including from a different host process."""
        if not (task_id or execution):
            raise ValueError("Cancellation needs a task or execution identity")
        where, values = ["status IN ('running','cancelling')"], []
        for key, value in (("chat", chat), ("id", task_id), ("execution", execution)):
            if value is not None:
                where.append(key + "=?")
                values.append(value)
        with _db(_database(), initialize=True) as db:
            rows = db.execute("SELECT id FROM tasks WHERE " + " AND ".join(where), values).fetchall()
            db.execute("UPDATE tasks SET status='cancelling' WHERE " + " AND ".join(where), values)
        return [row["id"] for row in rows]

    def recover_workspace(self, workspace):
        """A new chat must also detect jobs left by an older chat in this tree."""
        canonical = os.path.normcase(os.path.realpath(workspace))
        with _db(_database(), initialize=True) as db:
            rows = db.execute("SELECT id,chat FROM tasks WHERE workspace=? AND status IN ('running','cancelling','recovery_required')",
                              (canonical,)).fetchall()
        for row in rows:
            for task in self.list(row["chat"], row["id"]):
                if task["status"] == "recovery_required":
                    quarantine(canonical, "Background task outcome requires reconciliation: " + task["id"])

    def workspace_pending(self, workspace):
        with _db(_database(), initialize=True) as db:
            return db.execute("SELECT 1 FROM tasks WHERE workspace=? AND status IN ('running','cancelling','recovery_required') LIMIT 1",
                              (os.path.normcase(os.path.realpath(workspace)),)).fetchone() is not None

    def close_session(self, chat):
        with _db(_database(), initialize=True) as db:
            db.execute("INSERT OR REPLACE INTO task_sessions VALUES(?,1)", (chat,))
            db.execute("UPDATE tasks SET status='cancelling' WHERE chat=? AND status IN ('running','cancelling')", (chat,))
        with self._lock:
            self._closed_sessions.add(chat)
            for job in self._jobs.values():
                if job.chat == chat:
                    job.token.cancel()

    def wait_session_idle(self, chat, timeout=10):
        """Wait for the shared chat lease, including detached model transports."""
        from research_agent.config import get_data_dir
        deadline = time.monotonic() + timeout
        while True:
            probe = exclusive_request(str(get_data_dir() / "session_control" / hashlib.sha256(chat.encode()).hexdigest()), chat)
            try:
                probe.__enter__()
            except RuntimeError:
                pass
            else:
                try:
                    tasks = self.list(chat)
                    if any(task["status"] == "recovery_required" for task in tasks):
                        raise RuntimeError("Session has background outcomes requiring reconciliation")
                    if not any(task["status"] in ACTIVE for task in tasks):
                        return
                finally:
                    probe.__exit__(None, None, None)
            if time.monotonic() >= deadline:
                raise TimeoutError("Session still owns active requests or background tasks")
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))

    def stop(self):
        with self._lock:
            self._accepting = False
            for job in self._jobs.values():
                job.token.cancel()

    def drain(self, timeout=10):
        with self._lock:
            jobs = list(self._jobs.values())
        deadline = time.monotonic() + timeout
        for job in jobs:
            job.thread.join(max(0, deadline - time.monotonic()))
        if any(job.thread.is_alive() for job in jobs):
            raise TimeoutError("Background tasks have not finished cleanup; leases remain owned")
        with self._lock:
            if self._failures:
                raise RuntimeError("Background cleanup requires reconciliation: " + ", ".join(self._failures))

    def shutdown(self, timeout=10):
        self.stop()
        self.drain(timeout)


def recover_workspace(workspace):
    """Read-only controller instance; no worker or application lifetime is started."""
    BackgroundTasks().recover_workspace(workspace)
    from research_agent.checkpoint_events import consume_workspace
    consume_workspace(workspace)
