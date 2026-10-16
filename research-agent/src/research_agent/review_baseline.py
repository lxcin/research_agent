"""Persistent review baselines outside user workspaces."""
import hashlib
import json
import os
import sqlite3


def fingerprint(workspace, relative):
    from pathlib import Path
    root = Path(workspace).resolve()
    target = root / relative
    if not target.resolve().is_relative_to(root):
        raise ValueError("Review path escapes workspace")
    if target.is_symlink():
        return "symlink:" + os.readlink(target)
    if not target.exists():
        return None
    if not target.is_file():
        raise ValueError("Review target is not a regular file")
    return hashlib.sha256(target.read_bytes()).hexdigest()


def baseline(workspace, key, value=None):
    from research_agent.config import get_data_dir
    db = sqlite3.connect(get_data_dir() / "reviews.db", timeout=5)
    identity = os.path.normcase(os.path.realpath(workspace))
    try:
        db.execute("CREATE TABLE IF NOT EXISTS reviews(workspace TEXT, key TEXT, value TEXT, PRIMARY KEY(workspace,key))")
        if value is not None:
            db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?)", (identity, key, json.dumps(value)))
            db.commit()
        row = db.execute("SELECT value FROM reviews WHERE workspace=? AND key=?", (identity, key)).fetchone()
        return json.loads(row[0]) if row else {}
    finally:
        db.close()


def baseline_once(workspace, key, value):
    """Seal evidence once; a later refresh cannot approve intervening edits."""
    from research_agent.config import get_data_dir
    db = sqlite3.connect(get_data_dir() / "reviews.db", timeout=5)
    try:
        identity = os.path.normcase(os.path.realpath(workspace))
        db.execute("CREATE TABLE IF NOT EXISTS reviews(workspace TEXT,key TEXT,value TEXT,PRIMARY KEY(workspace,key))")
        db.execute("INSERT OR IGNORE INTO reviews VALUES(?,?,?)", (identity, key, json.dumps(value)))
        db.commit()
        return json.loads(db.execute("SELECT value FROM reviews WHERE workspace=? AND key=?", (identity, key)).fetchone()[0])
    finally:
        db.close()


def delete_baselines(workspace, keys):
    """Release only explicitly named evidence after its references are retired."""
    from research_agent.config import get_data_dir
    db = sqlite3.connect(get_data_dir() / "reviews.db", timeout=5)
    try:
        identity = os.path.normcase(os.path.realpath(workspace))
        db.executemany("DELETE FROM reviews WHERE workspace=? AND key=?", [(identity, key) for key in keys])
        db.commit()
    finally:
        db.close()
