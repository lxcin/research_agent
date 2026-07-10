"""Conversation storage: persists turns to SQLite and Chroma for semantic search."""
import json
import uuid
from datetime import datetime

from research_agent.models import ConversationTurn
from research_agent.store import _get_db, init_db
from research_agent.vector_store import add_chunks, get_collection


def init_conversation_table():
    db = _get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS conversation_turns (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            round_number INTEGER NOT NULL,
            user_message TEXT NOT NULL,
            assistant_message TEXT DEFAULT '',
            timestamp TEXT NOT NULL,
            compressed INTEGER DEFAULT 0,
            summary TEXT DEFAULT ''
        )
    """)
    db.commit()


def store_turn(project_id: str, round_number: int, user_msg: str, assistant_msg: str):
    init_db()
    init_conversation_table()
    db = _get_db()
    turn_id = str(uuid.uuid4())
    ts = datetime.now().isoformat()
    db.execute(
        "INSERT INTO conversation_turns (id, project_id, round_number, user_message, assistant_message, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
        (turn_id, project_id, round_number, user_msg, assistant_msg, ts)
    )
    db.commit()
    text = f"用户: {user_msg}\n助手: {assistant_msg}"
    add_chunks(f"conv_{project_id}", [{"chunk_index": round_number, "text": text}], collection_name="conversations")


def get_recent_turns(project_id: str, limit: int = 10) -> list[ConversationTurn]:
    init_db()
    init_conversation_table()
    db = _get_db()
    rows = db.execute(
        "SELECT id, project_id, round_number, user_message, assistant_message, timestamp, compressed, summary FROM conversation_turns WHERE project_id = ? ORDER BY round_number DESC LIMIT ?",
        (project_id, limit)
    ).fetchall()
    return [_row_to_turn(r) for r in reversed(rows)]


def get_all_turns(project_id: str) -> list[ConversationTurn]:
    init_db()
    init_conversation_table()
    db = _get_db()
    rows = db.execute(
        "SELECT id, project_id, round_number, user_message, assistant_message, timestamp, compressed, summary FROM conversation_turns WHERE project_id = ? ORDER BY round_number ASC",
        (project_id,)
    ).fetchall()
    return [_row_to_turn(r) for r in rows]


def count_uncompressed_turns(project_id: str) -> int:
    init_db()
    init_conversation_table()
    db = _get_db()
    row = db.execute(
        "SELECT COUNT(*) FROM conversation_turns WHERE project_id = ? AND compressed = 0",
        (project_id,)
    ).fetchone()
    return row[0] if row else 0


def mark_compressed(turn_ids: list[str], summary: str):
    db = _get_db()
    for tid in turn_ids:
        db.execute("UPDATE conversation_turns SET compressed = 1, summary = ? WHERE id = ?", (summary, tid))
    db.commit()


def _row_to_turn(row) -> ConversationTurn:
    return ConversationTurn(
        id=row[0], project_id=row[1], round_number=row[2],
        user_message=row[3], assistant_message=row[4], timestamp=row[5],
        compressed=bool(row[6]), summary=row[7] if len(row) > 7 else ""
    )