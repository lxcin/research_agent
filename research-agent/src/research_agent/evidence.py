"""Nonblocking lifecycle publisher and independently launched SQLite collector.

Run: python -m research_agent.evidence --database PATH
"""
import argparse
import json
import os
from pathlib import Path
import socket
import sqlite3
import time
import uuid
from contextlib import contextmanager


@contextmanager
def connection(*args, **kwargs):
    db = sqlite3.connect(*args, **kwargs)
    try:
        with db:
            yield db
    finally:
        db.close()


def database_path():
    from research_agent.config import get_data_dir
    return get_data_dir() / "evidence" / "lifecycle.db"


def initialize(db):
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS outbox(scope TEXT, sequence INTEGER, payload TEXT, PRIMARY KEY(scope, sequence))")
    db.execute("CREATE TABLE IF NOT EXISTS evidence(scope TEXT, sequence INTEGER, payload TEXT, PRIMARY KEY(scope, sequence))")


def publish(event: dict) -> None:
    """Commit a durable local outbox entry; background collector owns ingestion.

    Durability requires a small synchronous commit. Failure is explicit rather
    than silently converting storage failure into missing evidence.
    """
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with connection(path, timeout=5) as db:
        initialize(db)
        db.execute("INSERT OR IGNORE INTO outbox VALUES(?,?,?)",
                   (event["scope"], event["sequence"], json.dumps(event)))


def ingest_once(database):
    with connection(database, timeout=5) as db:
        initialize(db)
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT OR IGNORE INTO evidence SELECT * FROM outbox")
        count = db.execute("SELECT count(*) FROM outbox").fetchone()[0]
        db.execute("DELETE FROM outbox")
    return count


class EvidenceEmitter:
    def __init__(self, owner, lifetime):
        self.scope = uuid.uuid4().hex
        self.owner, self.lifetime = owner, lifetime
        self.sequence = 0

    def emit(self, kind):
        self.sequence += 1
        try:
            publish({"scope": self.scope, "owner": self.owner, "lifetime": self.lifetime,
                     "sequence": self.sequence, "kind": kind, "time": time.time()})
        except (OSError, sqlite3.Error):
            import logging
            logging.getLogger(__name__).exception("Evidence outbox commit failed: scope=%s sequence=%s", self.scope, self.sequence)


def collect(database, port=None):
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    while True:
        ingest_once(database)
        time.sleep(0.1)


def read(database):
    if not Path(database).exists():
        return {}
    grouped = {}
    with connection(f"file:{Path(database).as_posix()}?mode=ro", uri=True) as db:
        for (payload,) in db.execute("SELECT payload FROM evidence ORDER BY scope, sequence"):
            event = json.loads(payload)
            grouped.setdefault(event["scope"], []).append(event)
    pending = 0
    with connection(database) as db:
        if db.execute("SELECT name FROM sqlite_master WHERE name='outbox'").fetchone():
            pending = db.execute("SELECT count(*) FROM outbox").fetchone()[0]
    incomplete = 0
    complete = []
    sessions = {}
    for key, events in grouped.items():
        if events[0]["kind"] != "created" or events[-1]["kind"] != "closed":
            incomplete += 1
            continue
        if [e["sequence"] for e in events] != list(range(1, len(events) + 1)):
            incomplete += 1
            continue
        complete.append([e["kind"] for e in events])
        if events[0]["lifetime"] == "session":
            sessions[events[0]["owner"]] = key
    return {"resource_scopes": complete, "session_scopes": sessions,
            "evidence_health": {"pending": pending, "incomplete": incomplete, "complete": len(complete)}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--port", type=int, default=18799)
    parser.add_argument("--once", action="store_true", help="Ingest durable backlog and exit")
    args = parser.parse_args()
    if args.once:
        ingest_once(args.database)
    else:
        collect(args.database)
