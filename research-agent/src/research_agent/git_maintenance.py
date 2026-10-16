"""Durable, detached Git GC. One queue entry per shared object database.

No working-tree mutation, immediate pruning, reflog expiry, or forced GC.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from research_agent.git_environment import host_git_environment

DEBOUNCE = 30.0
COOLDOWN = 6 * 60 * 60.0
TIMEOUT = 300
MAX_ATTEMPTS = 5


def database_path():
    from research_agent.config import get_data_dir
    return get_data_dir().resolve() / "git_maintenance.db"


@contextmanager
def database(filename):
    db = sqlite3.connect(filename, timeout=0.1)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE TABLE IF NOT EXISTS jobs(
            id TEXT PRIMARY KEY, common_dir TEXT, identity TEXT,
            generation INTEGER, completed INTEGER DEFAULT 0, status TEXT,
            due REAL, last_success REAL DEFAULT 0, attempts INTEGER DEFAULT 0,
            error TEXT DEFAULT '')""")
        with db:
            yield db
    finally:
        db.close()


def _identity(path):
    info = Path(path).stat()
    return json.dumps([info.st_dev, info.st_ino])


def enqueue(workspace: str) -> dict:
    """Persist intent and launch a hidden worker; never run GC in the caller."""
    try:
        result = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                cwd=workspace, capture_output=True, text=True, timeout=10,
                                env=host_git_environment())
        if result.returncode:
            raise ValueError("Cannot resolve Git object database")
        common = os.path.normcase(os.path.realpath(result.stdout.strip()))
        identity = _identity(common)
        key = hashlib.sha256((common + "\0" + identity).encode()).hexdigest()
        filename, now = database_path(), time.time()
        with database(filename) as db:
            db.execute("""INSERT INTO jobs(id,common_dir,identity,generation,status,due)
                VALUES(?,?,?,1,'pending',?) ON CONFLICT(id) DO UPDATE SET
                generation=generation+1,
                status=CASE WHEN status='running' THEN 'running' ELSE 'pending' END,
                attempts=CASE WHEN status IN ('failed','blocked') THEN 0 ELSE attempts END,
                due=CASE WHEN status IN ('pending','running') THEN due ELSE MAX(?,last_success+?) END""",
                       (key, common, identity, now + DEBOUNCE, now + DEBOUNCE, COOLDOWN))
        try:
            launch_worker(filename)
        except OSError as exc:
            # Intent remains durable; resume on application startup or the next enqueue.
            with database(filename) as db:
                db.execute("UPDATE jobs SET error=? WHERE id=?", ("Worker launch failed: " + str(exc), key))
        return {"success": True, "job_id": key}
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
        logging.getLogger(__name__).warning("Git maintenance enqueue failed: %s", exc)
        return {"success": False, "reason": str(exc)}


def launch_worker(filename):
    from research_agent.execution import exclusive_request
    try:
        with exclusive_request(str(Path(filename).resolve()), "git-maintenance:" + str(Path(filename).resolve())):
            pass
    except RuntimeError:
        return None  # A worker already owns this installation's durable queue.
    return _launch_detached(filename)


def _launch_detached(filename):
    filename = Path(filename).resolve()
    env = dict(os.environ, RESEARCH_AGENT_DATA_DIR=str(filename.parent))
    # Source checkouts need the same import path as the host; installed wheels also work.
    source = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen([sys.executable, "-m", "research_agent.git_maintenance", "--database", str(filename)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=env, close_fds=True, start_new_session=os.name != "nt",
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def resume():
    filename = database_path()
    if not filename.exists():
        return
    try:
        with database(filename) as db:
            pending = db.execute("SELECT 1 FROM jobs WHERE status IN ('pending','running') LIMIT 1").fetchone()
        if pending:
            launch_worker(filename)
    except (OSError, sqlite3.Error) as exc:
        logging.getLogger(__name__).warning("Git maintenance resume failed: %s", exc)


def status():
    filename = database_path()
    if not filename.exists():
        return []
    with database(filename) as db:
        return [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY common_dir")]


def _gc(job):
    common = job["common_dir"]
    if os.path.normcase(os.path.realpath(common)) != common or _identity(common) != job["identity"]:
        raise ValueError("Repository identity changed; queued maintenance blocked")
    # Git's own gc.pid coordinates other installations and external GC. Never delete it.
    if (Path(common) / "gc.pid").exists():
        raise RuntimeError("Git GC lock exists; retry later")
    env = host_git_environment()
    result = subprocess.run([
        "git", "--git-dir", common,
        "-c", "gc.autoDetach=false", "-c", "gc.reflogExpire=never",
        "-c", "gc.reflogExpireUnreachable=never", "-c", "gc.worktreePruneExpire=never",
        "-c", "gc.packRefs=false", "-c", "gc.writeCommitGraph=false",
        "-c", "pack.threads=1", "-c", "pack.windowMemory=64m",
        "gc", "--quiet", "--no-aggressive", "--prune=2.weeks.ago",
    ], capture_output=True, text=True, timeout=TIMEOUT, env=env,
       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError((result.stderr or "Git GC failed")[-2000:])


def process_due(filename, *, now=None):
    """Process one due job. Caller holds the installation-wide worker OS lease."""
    now = time.time() if now is None else now
    with database(filename) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM jobs WHERE status IN ('pending','running') AND due<=? ORDER BY due LIMIT 1", (now,)).fetchone()
        if row is None:
            return False
        job = dict(row)
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (job["id"],))
    try:
        _gc(job)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        attempts = job["attempts"] + 1
        state = "blocked" if isinstance(exc, (ValueError, FileNotFoundError)) else "failed" if attempts >= MAX_ATTEMPTS else "pending"
        with database(filename) as db:
            db.execute("UPDATE jobs SET status=?,attempts=?,error=?,due=? WHERE id=?",
                       (state, attempts, str(exc), time.time() + min(3600, 60 * 2 ** (attempts - 1)), job["id"]))
    else:
        finished = time.time()
        with database(filename) as db:
            # New retirements arriving while GC runs need another pass, not a lost wakeup.
            db.execute("""UPDATE jobs SET completed=?,status=CASE WHEN generation>? THEN 'pending' ELSE 'idle' END,
                last_success=?,due=?,attempts=0,error='' WHERE id=?""",
                       (job["generation"], job["generation"], finished, finished + COOLDOWN, job["id"]))
    return True


def work(filename):
    from research_agent.execution import exclusive_request
    filename = Path(filename).resolve()
    if not filename.exists():
        return
    scope = exclusive_request(str(filename), "git-maintenance:" + str(filename))
    try:
        scope.__enter__()
    except RuntimeError:
        return
    try:
        while filename.exists():
            if process_due(filename):
                continue
            with database(filename) as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT MIN(due) FROM jobs WHERE status IN ('pending','running')").fetchone()
                if row[0] is None:
                    # Release the worker lease while holding the queue write transaction.
                    # A publisher can only commit new work after the lease is available,
                    # closing the empty-queue/worker-exit lost-wakeup window.
                    scope.__exit__(None, None, None)
                    scope = None
                    return
            time.sleep(min(1.0, max(0.05, row[0] - time.time())))
    finally:
        if scope is not None:
            scope.__exit__(None, None, None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    work(args.database)
