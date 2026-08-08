"""SQLite storage for Papers and Projects."""
import json
import sqlite3
import threading
from pathlib import Path

from research_agent.config import get_data_dir
from research_agent.models import Paper

_DB = None
_DB_LOCK = threading.Lock()


def _get_db() -> sqlite3.Connection:
    global _DB
    if _DB is None:
        db_path = get_data_dir() / "research_agent.db"
        _DB = sqlite3.connect(str(db_path), check_same_thread=False)
        _DB.row_factory = sqlite3.Row
        _DB.execute("PRAGMA journal_mode=WAL")
    return _DB


def init_db():
    db = _get_db()
    db.executescript("""
            CREATE TABLE IF NOT EXISTS papers (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            doi TEXT NOT NULL DEFAULT '',
            year INTEGER NOT NULL DEFAULT 0,
            source_score INTEGER NOT NULL DEFAULT 5,
            citation_count INTEGER NOT NULL DEFAULT 0,
            authors TEXT NOT NULL DEFAULT '[]',
            abstract TEXT NOT NULL DEFAULT '',
            file_path TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS chunk_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id_a TEXT NOT NULL,
            chunk_index_a INTEGER NOT NULL,
            paper_id_b TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT ''
        );
    """)
    db.commit()

    # Project-paper junction table
    db.execute("""
        CREATE TABLE IF NOT EXISTS project_papers (
            project_id TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            PRIMARY KEY (project_id, paper_id)
        )
    """)
    db.commit()


def link_paper_to_project(paper_id: str, project_id: str):
    db = _get_db()
    db.execute("INSERT OR IGNORE INTO project_papers (project_id, paper_id) VALUES (?, ?)",
               (project_id, paper_id))
    db.commit()


def get_project_papers(project_id: str) -> list[str]:
    db = _get_db()
    rows = db.execute("SELECT paper_id FROM project_papers WHERE project_id = ?",
                      (project_id,)).fetchall()
    return [r[0] for r in rows]


def init_conflict_table():
    db = _get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS chunk_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id_a TEXT NOT NULL,
            chunk_index_a INTEGER NOT NULL,
            paper_id_b TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT ''
        );
    """)
    db.commit()


def _paper_from_row(row) -> Paper:
    return Paper(
        id=row["id"],
        title=row["title"],
        doi=row["doi"],
        year=row["year"],
        source_score=row["source_score"],
        citation_count=row["citation_count"],
        authors=json.loads(row["authors"]),
        abstract=row["abstract"],
        file_path=row["file_path"],
    )


def insert_paper(paper: Paper) -> str:
    import uuid
    db = _get_db()
    paper_id = paper.id or str(uuid.uuid4())
    db.execute(
        "INSERT OR REPLACE INTO papers (id, title, doi, year, source_score, citation_count, authors, abstract, file_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (paper_id, paper.title, paper.doi, paper.year, paper.source_score,
         paper.citation_count, json.dumps(paper.authors), paper.abstract, paper.file_path),
    )
    db.commit()
    return paper_id


def get_paper(paper_id: str) -> Paper | None:
    db = _get_db()
    row = db.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    return _paper_from_row(row) if row else None


def get_all_papers() -> list[Paper]:
    db = _get_db()
    rows = db.execute("SELECT * FROM papers ORDER BY year DESC").fetchall()
    return [_paper_from_row(r) for r in rows]


def delete_paper(paper_id: str):
    db = _get_db()
    db.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
    db.commit()


