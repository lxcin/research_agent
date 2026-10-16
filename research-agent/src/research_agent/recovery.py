"""Operator reconciliation. No automatic retry of uncertain side effects."""
from contextlib import contextmanager
import json
import sqlite3
import time


@contextmanager
def database():
    from research_agent.config import get_data_dir
    path = get_data_dir() / "executions.db"
    if not path.exists():
        raise ValueError("No execution journal")
    db = sqlite3.connect(path, timeout=5)
    try:
        yield db
    finally:
        db.close()


def inspect_execution(run_id):
    with database() as db:
        row = db.execute("SELECT identity,payload FROM checkpoints WHERE run=?", (run_id,)).fetchone()
        if not row:
            raise ValueError("Execution checkpoint not found")
        identity = json.loads(row[0])
        payload = json.loads(row[1])
        exists = db.execute("SELECT name FROM sqlite_master WHERE name='calls'").fetchone()
        calls = db.execute("SELECT call,signature,status FROM calls WHERE run=?", (run_id,)).fetchall() if exists else []
        return {"execution_id": run_id, "message": identity[0], "workspace": identity[1],
                "chat_id": identity[2], "kernel": identity[3],
                "termination_reason": payload.get("reason"),
                "status": payload.get("interruption", {}).get("status", "completed" if "final" in payload else "interrupted"),
                "phase": payload.get("phase"), "rounds": payload.get("rounds", 0),
                "calls": [{"call_id": key, "tool": json.loads(signature)[0], "status": status}
                          for key, signature, status in calls]}


def reconcile(run_id, call_id, *, decision, note, result=None):
    if not note.strip():
        raise ValueError("Reconciliation requires evidence/reason")
    if decision not in ("completed", "not_executed"):
        raise ValueError("Unsupported reconciliation decision")
    info = inspect_execution(run_id)
    from research_agent.execution import exclusive_request
    from research_agent.tools.schema import ToolResult
    from dataclasses import asdict
    if decision == "completed":
        if not isinstance(result, ToolResult) or not isinstance(result.success, bool):
            raise ValueError("Verified completion requires a ToolResult")
        encoded = json.dumps({"tool_result": asdict(result)})
    else:
        encoded = None
    with exclusive_request(info["workspace"], info["chat_id"]), database() as db:
        db.execute("CREATE TABLE IF NOT EXISTS reconciliations(run TEXT, call TEXT, decision TEXT, note TEXT, time REAL)")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status FROM calls WHERE run=? AND call=?", (run_id, call_id)).fetchone()
        if not row or row[0] != "started":
            raise ValueError("Only uncertain started calls may be reconciled")
        if decision == "completed":
            db.execute("UPDATE calls SET status='completed',result=? WHERE run=? AND call=?", (encoded, run_id, call_id))
        else:
            db.execute("DELETE FROM calls WHERE run=? AND call=?", (run_id, call_id))
        db.execute("INSERT INTO reconciliations VALUES(?,?,?,?,?)", (run_id, call_id, decision, note, time.time()))
        db.commit()
    return {"status": "reconciled", "decision": decision, "execution_id": run_id, "call_id": call_id}
