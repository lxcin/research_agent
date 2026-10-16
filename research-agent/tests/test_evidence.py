import json
import sqlite3
from research_agent.evidence import read


def test_reader_only_accepts_closed_contiguous_traces(tmp_path):
    path = tmp_path / "events.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE evidence(scope TEXT, sequence INTEGER, payload TEXT)")
        for key, events in {"complete": [(1, "created"), (2, "drain"), (3, "cleanup"), (4, "closed")],
                            "gap": [(1, "created"), (3, "closed")],
                            "running": [(1, "created")]}.items():
            for sequence, kind in events:
                db.execute("INSERT INTO evidence VALUES(?,?,?)", (key, sequence, json.dumps(
                    {"scope": key, "sequence": sequence, "kind": kind, "owner": key, "lifetime": "application"})))
    assert read(path)["resource_scopes"] == [["created", "drain", "cleanup", "closed"]]
