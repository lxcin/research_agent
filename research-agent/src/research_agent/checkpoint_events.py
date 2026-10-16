"""Durable snapshot outbox: immutable evidence, at-least-once delivery.

The last writer captures E before unlocking. Consumers publish that E, never
recapture a workspace after a delayed completion message.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sqlite3


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS snapshot_runs(
        id TEXT PRIMARY KEY, execution TEXT NOT NULL, workspace TEXT NOT NULL,
        ref TEXT NOT NULL, foreground_closed INTEGER DEFAULT 0,
        safe INTEGER DEFAULT 0, status TEXT DEFAULT 'waiting', error TEXT DEFAULT '')""")
    db.execute("""CREATE TABLE IF NOT EXISTS snapshot_events(
        id TEXT PRIMARY KEY, run TEXT NOT NULL, kind TEXT NOT NULL,
        payload TEXT NOT NULL, status TEXT DEFAULT 'pending', error TEXT DEFAULT '')""")


def _path():
    from research_agent.background_tasks import _database
    return _database()


def _canonical(workspace):
    return os.path.normcase(os.path.realpath(workspace))


def register_run(state, workspace, ref, sha):
    lease = getattr(state, "_request_lease", None)
    execution = getattr(state, "_execution_id", "")
    if lease is None or not execution:
        return  # Standalone/manual checkpoints have no trusted writer lease.
    from research_agent.background_tasks import _db
    path = _path()
    with _db(path, initialize=True) as db:
        old = db.execute("SELECT execution,workspace,ref FROM snapshot_runs WHERE id=?", (sha,)).fetchone()
        if old is not None and tuple(old) != (execution, _canonical(workspace), ref):
            raise ValueError("Snapshot run identity changed")
        db.execute("""INSERT INTO snapshot_runs(id,execution,workspace,ref) VALUES(?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET foreground_closed=0,safe=0,status='waiting',error=''""",
                   (sha, execution, _canonical(workspace), ref))
        db.execute("DELETE FROM snapshot_events WHERE run=? AND kind='foreground_finished'", (sha,))
    lease._snapshot_run = (path, sha)
    lease.before_final_release(lambda: _last_writer(path, sha))


def task_finished(db, task_id, execution, checkpoint, status):
    """Called in the same transaction that records terminal task status."""
    if not checkpoint:
        return
    row = db.execute("SELECT id FROM snapshot_runs WHERE execution=? AND ref=?", (execution, checkpoint)).fetchone()
    if row is not None:
        payload = json.dumps({"task_id": task_id, "execution": execution, "status": status})
        db.execute("INSERT OR IGNORE INTO snapshot_events(id,run,kind,payload) VALUES(?,?,'task_finished',?)",
                   ("task:" + task_id, row["id"], payload))


def foreground_finished(lease, *, safe):
    descriptor = getattr(lease, "_snapshot_run", None)
    if descriptor is None:
        return
    from research_agent.background_tasks import _db
    path, run = descriptor
    try:
        with _db(path) as db:
            db.execute("UPDATE snapshot_runs SET foreground_closed=1,safe=? WHERE id=?", (bool(safe), run))
            db.execute("""INSERT OR IGNORE INTO snapshot_events(id,run,kind,payload)
                SELECT ?,?,'foreground_finished',? WHERE EXISTS(SELECT 1 FROM snapshot_runs WHERE id=?)""",
                       ("foreground:" + run, run, json.dumps({"safe": bool(safe)}), run))
    except (OSError, sqlite3.Error):
        logging.getLogger(__name__).exception("Snapshot foreground completion could not be persisted")


def foreground_suspended(lease):
    """The task may resume writing under the same S; do not seal an interim E."""
    descriptor = getattr(lease, "_snapshot_run", None)
    if descriptor is None:
        return
    from research_agent.background_tasks import _db
    path, run = descriptor
    with _db(path) as db:
        db.execute("UPDATE snapshot_runs SET foreground_closed=1,safe=0,status='suspended',error='' WHERE id=?", (run,))


def _eligible(db, row):
    if not row["foreground_closed"] or not row["safe"]:
        return False
    return db.execute("""SELECT 1 FROM tasks WHERE workspace=?
        AND status IN ('running','cancelling','recovery_required') LIMIT 1""", (row["workspace"],)).fetchone() is None


def _last_writer(path, run):
    from research_agent.background_tasks import _db
    from research_agent import checkpoint as cp
    from research_agent.review_baseline import baseline
    workspace = None
    try:
        with _db(path) as db:
            row = db.execute("SELECT * FROM snapshot_runs WHERE id=?", (run,)).fetchone()
            if row is None:
                return
            workspace = row["workspace"]
            if baseline(workspace, "active-turn").get("sha") != run:
                db.execute("UPDATE snapshot_events SET status='stale',payload='{}' WHERE run=?", (run,))
                db.execute("UPDATE snapshot_runs SET status='stale' WHERE id=?", (run,))
                return
            if row["status"] == "suspended":
                return
            if not _eligible(db, row):
                db.execute("UPDATE snapshot_runs SET status='recovery_required',error='Writer boundary not confirmed safe' WHERE id=?", (run,))
                return
            if baseline(workspace, "checkpoint:" + run):
                db.execute("UPDATE snapshot_runs SET status='sealed' WHERE id=?", (run,))
                db.execute("UPDATE snapshot_events SET status='done',payload='{}',error='' WHERE run=?", (run,))
                return
            db.execute("UPDATE snapshot_runs SET status='capturing' WHERE id=?", (run,))
        evidence = cp.capture_checkpoint_end(workspace, row["ref"])
        with _db(path) as db:
            # Git objects are pinned before committing the durable ready message.
            db.execute("INSERT OR IGNORE INTO snapshot_events(id,run,kind,payload) VALUES(?,?,'snapshot_ready',?)",
                       ("ready:" + run, run, json.dumps(evidence)))
            db.execute("UPDATE snapshot_runs SET status='ready' WHERE id=?", (run,))
        consume_workspace(workspace, locked=True, path=path)
    except Exception as exc:
        with _db(path) as db:
            # A ready message is replayable even if publishing/acknowledging failed.
            ready = db.execute("SELECT 1 FROM snapshot_events WHERE id=?", ("ready:" + run,)).fetchone()
            db.execute("UPDATE snapshot_runs SET status=?,error=? WHERE id=?",
                       ("ready" if ready else "undo_unavailable", str(exc), run))
        logging.getLogger(__name__).exception("Snapshot finalization deferred: %s", workspace)


def _consume_locked(workspace, path):
    from research_agent.background_tasks import _db
    from research_agent import checkpoint as cp
    from research_agent.review_baseline import baseline
    with _db(path) as db:
        rows = db.execute("SELECT * FROM snapshot_runs WHERE workspace=? AND status IN ('ready','capturing')", (_canonical(workspace),)).fetchall()
    for row in rows:
        with _db(path) as db:
            event = db.execute("SELECT * FROM snapshot_events WHERE id=?", ("ready:" + row["id"],)).fetchone()
            if event is None:
                # No E evidence survived. Re-reading current files would approve human edits.
                db.execute("UPDATE snapshot_runs SET status='undo_unavailable',error='Capture interrupted before durable ready evidence' WHERE id=?", (row["id"],))
                continue
            if event["status"] == "done":
                continue
            active = baseline(workspace, "active-turn")
            if active.get("sha") != row["id"]:
                db.execute("UPDATE snapshot_events SET status='stale',payload='{}' WHERE run=?", (row["id"],))
                db.execute("UPDATE snapshot_runs SET status='stale' WHERE id=?", (row["id"],))
                continue
            if not _eligible(db, row):
                db.execute("UPDATE snapshot_runs SET status='recovery_required',error='Unconfirmed writers or outcomes' WHERE id=?", (row["id"],))
                continue
        evidence = json.loads(event["payload"])
        result = cp.publish_checkpoint_end(workspace, row["ref"], evidence)
        if not result["success"]:
            raise RuntimeError(result["reason"])
        with _db(path) as db:
            db.execute("UPDATE snapshot_events SET status='done',payload='{}',error='' WHERE run=?", (row["id"],))
            db.execute("UPDATE snapshot_runs SET status='sealed',error='' WHERE id=?", (row["id"],))


def consume_workspace(workspace, *, locked=False, path=None):
    from research_agent.background_tasks import _db
    from research_agent.execution import exclusive_request
    from contextlib import nullcontext
    path = path or _path()
    if not Path(path).exists():
        return
    try:
        with _db(path, initialize=True):
            pass
        with nullcontext() if locked else exclusive_request(workspace, "snapshot-events:" + _canonical(workspace)):
            _consume_locked(workspace, path)
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        try:
            with _db(path) as db:
                db.execute("UPDATE snapshot_runs SET error=? WHERE workspace=? AND status IN ('ready','capturing')",
                           (str(exc), _canonical(workspace)))
                db.execute("UPDATE snapshot_events SET error=? WHERE status='pending' AND run IN (SELECT id FROM snapshot_runs WHERE workspace=?)",
                           (str(exc), _canonical(workspace)))
        except (OSError, sqlite3.Error):
            logging.getLogger(__name__).exception("Snapshot replay failure could not be persisted")
        logging.getLogger(__name__).info("Snapshot event replay deferred: %s", exc)


def resume():
    from research_agent.background_tasks import _db
    path = _path()
    if not path.exists():
        return
    try:
        with _db(path, initialize=True) as db:
            rows = db.execute("SELECT DISTINCT workspace FROM snapshot_runs WHERE status IN ('ready','capturing')").fetchall()
        for row in rows:
            consume_workspace(row["workspace"], path=path)
    except (OSError, sqlite3.Error):
        logging.getLogger(__name__).exception("Snapshot startup replay could not inspect its queue")


def status(workspace):
    from research_agent.background_tasks import _db
    with _db(_path(), initialize=True) as db:
        return [dict(row) for row in db.execute("SELECT * FROM snapshot_runs WHERE workspace=?", (_canonical(workspace),))]


def retire_previous(workspace, keep):
    """Retire all old undo deliveries after admitting and saving a new turn.

    Writer admission is checked separately before creating S. Missing E and
    failed delivery no longer pin old references or payloads indefinitely.
    """
    from research_agent.background_tasks import _db
    path = _path()
    if not path.exists():
        return
    with _db(path, initialize=True) as db:
        db.execute("""DELETE FROM snapshot_events WHERE run IN
            (SELECT id FROM snapshot_runs WHERE workspace=? AND id<>?)""",
                   (_canonical(workspace), keep))
        db.execute("DELETE FROM snapshot_runs WHERE workspace=? AND id<>?",
                   (_canonical(workspace), keep))
