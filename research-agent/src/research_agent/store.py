"""SQLite storage for Papers and Projects."""
import json
import sqlite3
import threading
from pathlib import Path

from research_agent.config import get_data_dir
from research_agent.models import Paper, Project, PendingTask, PlanStep, AccumulatedWisdom, ProjectStatus

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


def _execute(sql: str, params=()):
    with _DB_LOCK:
        db = _get_db()
        return db.execute(sql, params)


def _executescript(sql: str):
    with _DB_LOCK:
        db = _get_db()
        return db.executescript(sql)


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
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            topic TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            pending_task TEXT,
            history_summary TEXT NOT NULL DEFAULT '',
            intro_summary TEXT NOT NULL DEFAULT '',
            accumulated_wisdom TEXT NOT NULL DEFAULT '{}',
            plan TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
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

    # Migration: add workspace_dir column if missing
    try:
        db.execute("ALTER TABLE projects ADD COLUMN workspace_dir TEXT DEFAULT ''")
        db.commit()
    except Exception:
        pass

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


def _project_from_row(row) -> Project:
    plan_raw = json.loads(row["plan"])
    wisdom_data = json.loads(row["accumulated_wisdom"]) if row["accumulated_wisdom"] else {}
    return Project(
        id=row["id"],
        topic=row["topic"],
        status=ProjectStatus(row["status"]),
        pending_task=PendingTask(**json.loads(row["pending_task"])) if row["pending_task"] else None,
        history_summary=row["history_summary"],
        intro_summary=row["intro_summary"],
        accumulated_wisdom=AccumulatedWisdom(**wisdom_data) if wisdom_data else AccumulatedWisdom(),
        plan=[PlanStep(**s) for s in plan_raw],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        workspace_dir=row["workspace_dir"] if "workspace_dir" in row.keys() else "",
    )


def insert_project(project: Project) -> str:
    import uuid
    from datetime import datetime
    db = _get_db()
    project_id = project.id or str(uuid.uuid4())
    now = datetime.now().isoformat()
    db.execute(
        "INSERT OR REPLACE INTO projects (id, topic, status, pending_task, history_summary, intro_summary, accumulated_wisdom, plan, workspace_dir, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (project_id, project.topic, project.status.value if isinstance(project.status, ProjectStatus) else project.status,
         json.dumps(project.pending_task.__dict__) if project.pending_task else None,
         project.history_summary,
         project.intro_summary,
         json.dumps(project.accumulated_wisdom.__dict__) if project.accumulated_wisdom else "{}",
         json.dumps([s.__dict__ for s in project.plan]),
         getattr(project, 'workspace_dir', '') or '',
         project.created_at or now, now),
    )
    db.commit()
    return project_id


def get_project(project_id: str) -> Project | None:
    db = _get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return _project_from_row(row) if row else None


def get_all_projects() -> list[Project]:
    db = _get_db()
    rows = db.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
    return [_project_from_row(r) for r in rows]


def update_project(project: Project):
    insert_project(project)


def delete_project(project_id: str):
    with _DB_LOCK:
        db = _get_db()
        db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        db.commit()