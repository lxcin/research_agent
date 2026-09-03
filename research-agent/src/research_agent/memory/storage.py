"""Tier B memory SQLite backend.

Stores MemoryUnit rows in data_dir/memory/memory_units.db. Provides CRUD,
supersede-chain management, scope/kind filtering, and keyword ranking search
that serves as the always-available retrieval path (vector layer optional).
"""
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from research_agent.config import get_data_dir
from research_agent.memory.models import MemoryUnit, MemoryScope, MemoryKind, row_to_unit

_DB: sqlite3.Connection | None = None
_LOCK = threading.Lock()


def _get_db() -> sqlite3.Connection:
    global _DB
    if _DB is None:
        mem_dir = get_data_dir() / "memory"
        os.makedirs(mem_dir, exist_ok=True)
        db_path = str(mem_dir / "memory_units.db")
        _DB = sqlite3.connect(db_path, check_same_thread=False)
        _DB.row_factory = sqlite3.Row
        _DB.execute("PRAGMA journal_mode=WAL")
        init_db()
    return _DB


def init_db():
    db = _get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS memory_units (
            id TEXT PRIMARY KEY,
            scope TEXT NOT NULL DEFAULT 'user',
            kind TEXT NOT NULL DEFAULT 'fact',
            text TEXT NOT NULL,
            importance REAL NOT NULL DEFAULT 0.5,
            source TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            superseded_by TEXT NOT NULL DEFAULT '',
            embedding_id TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_mem_scope ON memory_units (scope, active)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_mem_kind ON memory_units (kind)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_mem_updated ON memory_units (updated_at)")
    db.commit()


def reset_db_for_tests():
    """Close + drop DB (used by test fixtures)."""
    global _DB
    if _DB is not None:
        try:
            _DB.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        try:
            _DB.close()
        except Exception:
            pass
        _DB = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert(unit: MemoryUnit) -> MemoryUnit:
    """Insert a unit, or update an existing one by id."""
    db = _get_db()
    if not unit.id:
        unit.id = f"mem_{uuid.uuid4().hex[:12]}"
    unit.updated_at = _now()
    with _LOCK:
        db.execute(
            "INSERT OR REPLACE INTO memory_units "
            "(id, scope, kind, text, importance, source, created_at, updated_at, superseded_by, embedding_id, active) "
            "VALUES (:id, :scope, :kind, :text, :importance, :source, :created_at, :updated_at, :superseded_by, :embedding_id, :active)",
            unit.to_row(),
        )
        db.commit()
    return unit


def get(unit_id: str) -> MemoryUnit | None:
    db = _get_db()
    row = db.execute("SELECT * FROM memory_units WHERE id = ?", (unit_id,)).fetchone()
    return row_to_unit(row) if row else None


def delete(unit_id: str) -> bool:
    db = _get_db()
    with _LOCK:
        cur = db.execute("DELETE FROM memory_units WHERE id = ?", (unit_id,))
        db.commit()
    return cur.rowcount > 0


def supersede(old_id: str, new_unit: MemoryUnit) -> MemoryUnit | None:
    """Mark old unit inactive/superseded and persist the new one. Returns new unit."""
    db = _get_db()
    old = get(old_id)
    if not old:
        return upsert(new_unit)
    # Persist new unit first so it has a real id to reference.
    saved = upsert(new_unit)
    saved.superseded_by = ""  # newest unit has no successor
    with _LOCK:
        db.execute(
            "UPDATE memory_units SET superseded_by = '' WHERE id = ?",
            (saved.id,),
        )
        db.execute(
            "UPDATE memory_units SET active = 0, superseded_by = ?, updated_at = ? WHERE id = ?",
            (saved.id, _now(), old_id),
        )
        db.commit()
    return saved


def list_units(scope: MemoryScope | None = None, kind: MemoryKind | None = None,
               active_only: bool = True, limit: int = 100) -> list[MemoryUnit]:
    db = _get_db()
    sql = "SELECT * FROM memory_units WHERE 1=1"
    args: list = []
    if active_only:
        sql += " AND active = 1"
    if scope:
        sql += " AND scope = ?"
        args.append(scope.value)
    if kind:
        sql += " AND kind = ?"
        args.append(kind.value)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    args.append(limit)
    rows = db.execute(sql, args).fetchall()
    return [row_to_unit(r) for r in rows]


def count() -> int:
    db = _get_db()
    return db.execute("SELECT COUNT(*) FROM memory_units").fetchone()[0]


# ── Keyword ranking (always-available retrieval path) ───────────────────────

def _tokenize(text: str) -> list[str]:
    if any('\u4e00' <= c <= '\u9fff' for c in text):
        import jieba
        tokens = list(jieba.cut(text))
        tokens += re.findall(r'[a-zA-Z0-9]+', text.lower())
    else:
        tokens = re.findall(r'[a-zA-Z0-9]+', text.lower())
    return [t for t in tokens if t.strip()]


def search_keyword(query: str, scope: MemoryScope | None = None,
                   kind: MemoryKind | None = None, limit: int = 5) -> list[MemoryUnit]:
    """Rank active units by query-term coverage + importance + recency."""
    q_terms = set(_tokenize(query))
    if not q_terms:
        return []

    best_score = 0.0
    scored = []
    try:
        now_ts = datetime.now(timezone.utc)
        for unit in list_units(scope=scope, kind=kind, active_only=True, limit=500):
            doc_terms = set(_tokenize(unit.text))
            present = doc_terms & q_terms
            if not present:
                continue
            coverage = len(present) / len(q_terms)
            base = 0.6 * coverage + 0.4 * unit.importance
            # recency bonus: 1/(1+days_old), capped modestly
            try:
                created = datetime.fromisoformat(unit.created_at)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                days = max(0.0, (now_ts - created).total_seconds() / 86400.0)
            except Exception:
                days = 0.0
            recency = 1.0 / (1.0 + days * 0.1)
            score = base * (0.85 + 0.15 * recency)
            unit.score = round(score, 4)
            scored.append(unit)
            best_score = max(best_score, score)
    except Exception:
        return []
    if not scored:
        return []
    # normalize so top result ~= 1
    for u in scored:
        u.score = round(u.score / best_score, 4) if best_score else u.score
    scored.sort(key=lambda u: u.score, reverse=True)
    return scored[:limit]
