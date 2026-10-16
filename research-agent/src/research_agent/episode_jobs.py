"""Durable episode jobs; model clients and credentials are never serialized."""
import json
import sqlite3
from contextlib import contextmanager
from research_agent.execution import exclusive_request


def path():
    from research_agent.config import get_data_dir
    return get_data_dir() / "episode_jobs.db"


@contextmanager
def database(filename):
    db = sqlite3.connect(filename, timeout=5)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, payload TEXT, status TEXT, error TEXT)")
        with db:
            yield db
    finally:
        db.close()


def enqueue(key, payload):
    filename = path()
    encoded = json.dumps(payload, ensure_ascii=False)
    with database(filename) as db:
        row = db.execute("SELECT payload,status FROM jobs WHERE id=?", (key,)).fetchone()
        if row and row[0] != encoded:
            raise ValueError("Episode execution id reused with different input")
        if not row:
            db.execute("INSERT INTO jobs VALUES(?,?,'pending','')", (key, encoded))
    return filename, key


def pending(retry_failed=False):
    filename = path()
    with database(filename) as db:
        statuses = ("pending", "running", "failed") if retry_failed else ("pending", "running")
        rows = db.execute("SELECT id FROM jobs WHERE status IN (%s)" % ",".join("?" for _ in statuses), statuses).fetchall()
    return [(filename, row[0]) for row in rows]


def process(filename, key, invoke, *, retry_failed=False):
    # OS locks disappear on process exit; running jobs can then be reclaimed.
    with exclusive_request(str(filename) + key, "episode:" + key):
        with database(filename) as db:
            row = db.execute("SELECT payload,status FROM jobs WHERE id=?", (key,)).fetchone()
            if row[1] == "completed" or (row[1] == "failed" and not retry_failed):
                return
            payload = json.loads(row[0])
            db.execute("UPDATE jobs SET status='running',error='' WHERE id=?", (key,))
        try:
            invoke(payload)
        except Exception as exc:
            with database(filename) as db:
                db.execute("UPDATE jobs SET status='failed',error=? WHERE id=?", (str(exc), key))
            raise
        with database(filename) as db:
            db.execute("UPDATE jobs SET status='completed' WHERE id=?", (key,))
