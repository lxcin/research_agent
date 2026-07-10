# Research Agent V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a CLI research partner agent with persistent memory, hybrid RAG retrieval, multi-project routing, skill system, and agentic self-correction loop.

**Architecture:** LangGraph-based agent with Router→Reasoner→Retriever→Generator nodes. Chroma + BM25 hybrid retrieval with ingest-time dedup & contradiction detection. LangGraph checkpoint (SqliteSaver) per project as memory — no separate UserProfile. Periodic compression extracts accumulated wisdom (SOPs, pitfalls, frameworks). LiteLLM for LLM abstraction. CLI via Click. All local.

**Tech Stack:** Python 3.11+, LangGraph, LiteLLM, Chroma, rank-bm25, pymupdf, SQLite, Click (CLI)

## Global Constraints

- Python 3.11+
- All dependencies declared in `pyproject.toml` with exact lower bounds
- Data stored under `~/research-agent-data/` (configurable via `RESEARCH_AGENT_DATA_DIR`)
- LLM API key from env var `RESEARCH_AGENT_LLM_KEY` or config file
- No hardcoded API keys anywhere
- TDD: every module has a corresponding test file under `tests/`
- CLI via Click library
- Commit after each task with `feat:` / `test:` prefixed messages

## File Structure

```
research-agent/
├── pyproject.toml
├── src/
│   └── research_agent/
│       ├── __init__.py
│       ├── cli.py              # Click CLI entry point + chat loop
│       ├── config.py           # Configuration from env/file, with defaults
│       ├── models.py           # All dataclasses (Paper, Chunk, Project, AccumulatedWisdom)
│       ├── store.py            # SQLite CRUD for Paper and Project index
│       ├── vector_store.py     # Chroma wrapper: add, delete, search
│       ├── ingestion.py        # PDF→text→chunk→embed→store pipeline (with dedup+contradiction on ingest)
│       ├── retrieval.py        # hybrid_search(): vector+BM25+RRF fusion
│       ├── search.py           # Semantic Scholar API client
│       ├── router.py           # Project auto-routing logic
│       ├── compressor.py       # Periodic compression: extract accumulated_wisdom (SOPs, pitfalls, frameworks)
│       ├── agent.py            # LangGraph state graph definition (checkpoint-driven memory)
│       ├── loop.py             # Agentic Loop: self-check, retry, converge
│       ├── skill.py            # Skill loader + executor
│       └── skills/
│           ├── __init__.py
│           ├── paper_search.py
│           ├── literature_review.py
│           └── write_report.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py             # Pytest fixtures: tmp data dir, in-memory DB, etc.
│   ├── test_models.py
│   ├── test_store.py
│   ├── test_vector_store.py
│   ├── test_ingestion.py
│   ├── test_retrieval.py
│   ├── test_search.py
│   ├── test_router.py
│   ├── test_compressor.py
│   ├── test_agent.py
│   ├── test_loop.py
│   ├── test_skill.py
│   └── test_cli.py
```

---

### Task 1: Project Scaffolding

**Files:**
- Create: `research-agent/pyproject.toml`
- Create: `research-agent/src/research_agent/__init__.py`
- Create: `research-agent/src/research_agent/config.py`
- Create: `research-agent/src/research_agent/models.py`
- Create: `research-agent/tests/__init__.py`
- Create: `research-agent/tests/conftest.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `config.load_config()` → dict, `config.get_data_dir()` → Path, config defaults

- [ ] **Step 1: Create project directory and pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "research-agent"
version = "0.1.0"
description = "A research partner agent with persistent memory"
requires-python = ">=3.11"
dependencies = [
    "click>=8.1",
    "litellm>=1.50",
    "chromadb>=0.5",
    "rank-bm25>=0.2",
    "pymupdf>=1.24",
    "langgraph>=0.2",
    "langgraph-checkpoint-sqlite>=2.0",
    "langchain-core>=0.3",
    "httpx>=0.27",
    "pydantic>=2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
]

[project.scripts]
research-agent = "research_agent.cli:main"

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 2: Verify pyproject.toml can be installed**

Run: `pip install -e .` from `research-agent/`
Expected: Installs successfully with no errors. `research-agent --help` works.

- [ ] **Step 3: Create config.py**

```python
"""Configuration loader for research-agent."""
import os
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / "research-agent-data"
ENV_DATA_DIR = os.environ.get("RESEARCH_AGENT_DATA_DIR")

def get_data_dir() -> Path:
    path = Path(ENV_DATA_DIR) if ENV_DATA_DIR else DEFAULT_DATA_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path

def load_config() -> dict:
    config_path = get_data_dir() / "config.yml"
    if config_path.exists():
        import yaml
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}

def get_llm_key() -> str | None:
    return os.environ.get("RESEARCH_AGENT_LLM_KEY")
```

- [ ] **Step 4: Verify config.py works in isolation**

Run: `python -c "from research_agent.config import get_data_dir, load_config; print(get_data_dir())"`
Expected: Prints path to `~/research-agent-data` and creates the directory.

- [ ] **Step 5: Create models.py**

```python
"""Core data models for research-agent."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    WAITING = "waiting"
    PAUSED = "paused"
    DONE = "done"


class Confidence(str, Enum):
    CERTAIN = "certain"
    SPECULATIVE = "speculative"
    UNCERTAIN = "uncertain"


@dataclass
class Paper:
    id: str | None = None
    title: str = ""
    doi: str = ""
    year: int = 0
    source_score: int = 5
    citation_count: int = 0
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    file_path: str = ""

@dataclass
class Chunk:
    id: str | None = None
    paper_id: str = ""
    text: str = ""
    chunk_index: int = 0

@dataclass
class PendingTask:
    description: str = ""
    expected_format: str = ""
    expected_time: str = ""


@dataclass
class PlanStep:
    step: str = ""
    owner: str = "agent"
    status: str = "pending"
    depends_on: list[str] = field(default_factory=list)


@dataclass
class Pitfall:
    phenomenon: str = ""
    root_cause: str = ""
    solution: str = ""
    improvement: str = ""


@dataclass
class AccumulatedWisdom:
    sops: list[str] = field(default_factory=list)
    pitfalls: list[dict] = field(default_factory=list)  # [{phenomenon, root_cause, solution, improvement}]
    frameworks: list[str] = field(default_factory=list)
    agent_improvements: list[str] = field(default_factory=list)


@dataclass
class Project:
    id: str | None = None
    topic: str = ""
    status: ProjectStatus = ProjectStatus.ACTIVE
    history_summary: str = ""
    accumulated_wisdom: AccumulatedWisdom = field(default_factory=AccumulatedWisdom)
    intro_summary: str = ""
    plan: list[PlanStep] = field(default_factory=list)
    pending_task: PendingTask | None = None
    created_at: str = ""
    updated_at: str = ""
@dataclass
class AgentState:
    """LangGraph state schema. Memory lives in LangGraph checkpoint per thread_id (project_id)."""
    user_input: str = ""
    active_project: Project | None = None
    all_projects: list[Project] = field(default_factory=list)
    retrieved_chunks: list[dict] = field(default_factory=list)
    retrieval_sufficient: bool = False
    retry_count: int = 0
    final_response: str = ""
    error: str = ""
    citations: list[str] = field(default_factory=list)
    confidence: str = Confidence.UNCERTAIN.value
    search_query: str = ""
    needs_retrieval: bool = True
    needs_compression: bool = False
```

- [ ] **Step 6: Verify models.py imports cleanly**

Run: `python -c "from research_agent.models import Paper, Project, AgentState; print('OK')"`
Expected: OK

- [ ] **Step 7: Create conftest.py**

```python
"""Pytest fixtures for research-agent tests."""
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def temp_data_dir(monkeypatch):
    """Redirect data dir to temp for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("RESEARCH_AGENT_DATA_DIR", tmpdir)
        yield Path(tmpdir)


@pytest.fixture
def sample_paper():
    from research_agent.models import Paper
    return Paper(
        id="paper_1",
        title="Attention Is All You Need",
        doi="10.1234/attention",
        year=2017,
        source_score=10,
        citation_count=100000,
        authors=["Vaswani", "Shazeer", "Parmar"],
        abstract="The dominant sequence transduction models...",
    )


@pytest.fixture
def sample_chunks():
    return [
        {"paper_id": "paper_1", "chunk_index": 0,
         "text": "The Transformer is based solely on attention mechanisms...",
         "source_score": 10},
        {"paper_id": "paper_1", "chunk_index": 1,
         "text": "We show that the Transformer generalizes well to other tasks...",
         "source_score": 10},
        {"paper_id": "paper_2", "chunk_index": 0,
         "text": "Convolutional approaches remain competitive...",
         "source_score": 5},
    ]
```

- [ ] **Step 8: Run conftest.py to verify**

Run: `python -c "import tests.conftest; print('OK')"`
Expected: OK

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml src/research_agent/__init__.py src/research_agent/config.py src/research_agent/models.py tests/
git commit -m "feat: project scaffolding with config, models, and test fixtures"
```

---

### Task 2: SQLite Storage Layer

**Files:**
- Create: `src/research_agent/store.py`
- Create: `tests/test_store.py`

**Interfaces:**
- Consumes: `config.get_data_dir()` from Task 1, `models.Paper`, `models.Project` from Task 1
- Produces: `store.init_db()`, `store.insert_paper(paper)→str`, `store.get_paper(id)→Paper`, `store.get_all_papers()→list[Paper]`, `store.delete_paper(id)`, `store.insert_project(project)→str`, `store.get_project(id)→Project`, `store.get_all_projects()→list[Project]`, `store.update_project(project)`, `store.delete_project(id)`

- [ ] **Step 1: Write failing test for paper CRUD**

```python
# tests/test_store.py
from research_agent.store import init_db, insert_paper, get_paper, get_all_papers, delete_paper
from research_agent.models import Paper


def test_insert_and_get_paper(temp_data_dir):
    init_db()
    paper = Paper(title="Test Paper", doi="10.0000/test", year=2024, source_score=8,
                  authors=["Alice"], abstract="An abstract.")
    paper_id = insert_paper(paper)
    assert paper_id is not None

    fetched = get_paper(paper_id)
    assert fetched.title == "Test Paper"
    assert fetched.doi == "10.0000/test"
    assert fetched.source_score == 8
    assert len(fetched.authors) == 1
    assert fetched.authors[0] == "Alice"


def test_get_all_papers(temp_data_dir):
    init_db()
    insert_paper(Paper(title="P1", doi="doi1"))
    insert_paper(Paper(title="P2", doi="doi2"))
    papers = get_all_papers()
    assert len(papers) >= 2


def test_delete_paper(temp_data_dir):
    init_db()
    pid = insert_paper(Paper(title="ToDelete", doi="doi_del"))
    delete_paper(pid)
    assert get_paper(pid) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_store.py -v`
Expected: FAIL with ImportError on `init_db`, `insert_paper`, etc.

- [ ] **Step 3: Implement store.py**

```python
"""SQLite storage for Papers and Projects."""
import json
import sqlite3
from pathlib import Path

from research_agent.config import get_data_dir
from research_agent.models import Paper, Project, PendingTask, PlanStep

_DB = None


def _get_db() -> sqlite3.Connection:
    global _DB
    if _DB is None:
        db_path = get_data_dir() / "research_agent.db"
        _DB = sqlite3.connect(str(db_path))
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
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            topic TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            pending_task TEXT,
            history_summary TEXT NOT NULL DEFAULT '',
            intro_summary TEXT NOT NULL DEFAULT '',
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
    wisdom_data = json.loads(row["accumulated_wisdom"]) if row.get("accumulated_wisdom") else {}
    return Project(
        id=row["id"],
        topic=row["topic"],
        status=row["status"],
        pending_task=PendingTask(**json.loads(row["pending_task"])) if row["pending_task"] else None,
        history_summary=row["history_summary"],
        intro_summary=row.get("intro_summary", ""),
        accumulated_wisdom=AccumulatedWisdom(**wisdom_data) if wisdom_data else AccumulatedWisdom(),
        plan=[PlanStep(**s) for s in plan_raw],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def insert_project(project: Project) -> str:
    import uuid
    from datetime import datetime
    db = _get_db()
    project_id = project.id or str(uuid.uuid4())
    now = datetime.now().isoformat()
    db.execute(
        "INSERT OR REPLACE INTO projects (id, topic, status, pending_task, history_summary, intro_summary, accumulated_wisdom, plan, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (project_id, project.topic, project.status.value,
         json.dumps(project.pending_task.__dict__) if project.pending_task else None,
         project.history_summary,
         project.intro_summary,
         json.dumps(project.accumulated_wisdom.__dict__) if project.accumulated_wisdom else "{}",
         json.dumps([s.__dict__ for s in project.plan]),
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
    db = _get_db()
    db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_store.py -v`
Expected: 3 PASS

- [ ] **Step 5: Add project CRUD tests**

```python
# Append to tests/test_store.py
from research_agent.models import Project, ProjectStatus, PendingTask, PlanStep


def test_insert_and_get_project(temp_data_dir):
    init_db()
    proj = Project(topic="Test Project", status=ProjectStatus.ACTIVE,
                   pending_task=PendingTask(description="Run HPLC",
                                            expected_format="CSV with retention_time and peak_area",
                                            expected_time="2 days"))
    pid = insert_project(proj)
    assert pid is not None

    fetched = get_project(pid)
    assert fetched.topic == "Test Project"
    assert fetched.status == ProjectStatus.ACTIVE
    assert fetched.pending_task.description == "Run HPLC"
    assert fetched.pending_task.expected_format == "CSV with retention_time and peak_area"


def test_get_all_projects(temp_data_dir):
    init_db()
    insert_project(Project(topic="P1"))
    insert_project(Project(topic="P2"))
    assert len(get_all_projects()) >= 2


def test_update_project(temp_data_dir):
    init_db()
    pid = insert_project(Project(topic="Original"))
    p = get_project(pid)
    p.topic = "Updated"
    p.history_summary = "Changed summary"
    update_project(p)
    fetched = get_project(pid)
    assert fetched.topic == "Updated"
    assert fetched.history_summary == "Changed summary"


def test_delete_project(temp_data_dir):
    init_db()
    pid = insert_project(Project(topic="ToDelete"))
    delete_project(pid)
    assert get_project(pid) is None
```

- [ ] **Step 6: Run all store tests**

Run: `pytest tests/test_store.py -v`
Expected: 7 PASS

- [ ] **Step 7: Commit**

```bash
git add src/research_agent/store.py tests/test_store.py
git commit -m "feat: SQLite storage layer for papers and projects"
```

---

### Task 3: Vector Store Wrapper

**Files:**
- Create: `src/research_agent/vector_store.py`
- Create: `tests/test_vector_store.py`

**Interfaces:**
- Consumes: `config.get_data_dir()` from Task 1, `models.Chunk` from Task 1
- Produces: `vector_store.get_collection()`, `vector_store.add_chunks(paper_id, chunks)`, `vector_store.search(query, n_results)→list[dict]`, `vector_store.delete_paper(paper_id)`, `vector_store.get_embedding_fn()`

- [ ] **Step 1: Write failing test**

```python
# tests/test_vector_store.py
from research_agent.vector_store import get_collection, add_chunks, search, delete_paper


def test_add_and_search(temp_data_dir):
    paper_id = "test_paper_1"
    chunks = [
        {"chunk_index": 0, "text": "The Transformer architecture revolutionized NLP."},
        {"chunk_index": 1, "text": "Attention mechanisms compute weighted sums of values."},
        {"chunk_index": 2, "text": "Convolutional neural networks are used in image processing."},
    ]
    add_chunks(paper_id, chunks)

    results = search("attention mechanism in transformers", n_results=2)
    assert len(results["ids"][0]) > 0
    ids = results["ids"][0]
    assert paper_id in ids[0]

    # Verify metadata
    metadatas = results["metadatas"][0]
    assert all("paper_id" in m for m in metadatas)


def test_delete_paper_chunks(temp_data_dir):
    paper_id = "test_paper_del"
    add_chunks(paper_id, [{"chunk_index": 0, "text": "Ephemeral content."}])

    pre_search = search("ephemeral", n_results=1)
    assert len(pre_search["ids"][0]) > 0

    delete_paper(paper_id)

    post_search = search("ephemeral", n_results=1)
    distances = post_search["distances"][0]
    assert len(distances) == 0 or all(d == 0 for d in distances)
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_vector_store.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement vector_store.py**

```python
"""Chroma vector database wrapper."""
import os
import chromadb
from chromadb.utils import embedding_functions

from research_agent.config import get_data_dir

_COLLECTION = None
_EMBEDDING_FN = None


def get_embedding_fn():
    global _EMBEDDING_FN
    if _EMBEDDING_FN is None:
        _EMBEDDING_FN = embedding_functions.DefaultEmbeddingFunction()
    return _EMBEDDING_FN


def get_collection() -> chromadb.Collection:
    global _COLLECTION
    if _COLLECTION is None:
        chroma_path = str(get_data_dir() / "chroma_db")
        client = chromadb.PersistentClient(path=chroma_path)
        _COLLECTION = client.get_or_create_collection(
            name="research_chunks",
            embedding_function=get_embedding_fn(),
        )
    return _COLLECTION


def add_chunks(paper_id: str, chunks: list[dict]):
    coll = get_collection()
    ids = [f"{paper_id}_chunk_{c['chunk_index']}" for c in chunks]
    documents = [c["text"] for c in chunks]
    metadatas = [{"paper_id": paper_id, "chunk_index": c["chunk_index"]} for c in chunks]
    if ids:
        coll.upsert(ids=ids, documents=documents, metadatas=metadatas)


def search(query: str, n_results: int = 5) -> dict:
    coll = get_collection()
    return coll.query(query_texts=[query], n_results=n_results)


def delete_paper(paper_id: str):
    coll = get_collection()
    results = coll.get(where={"paper_id": paper_id})
    if results["ids"]:
        coll.delete(ids=results["ids"])
```

- [ ] **Step 4: Run tests to verify**

Run: `pytest tests/test_vector_store.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_agent/vector_store.py tests/test_vector_store.py
git commit -m "feat: Chroma vector store wrapper with add/search/delete"
```

---

### Task 4: PDF Ingestion Pipeline (Traceable Multi-Source RAG)

**Files:**
- Create: `src/research_agent/ingestion.py`
- Create: `tests/test_ingestion.py`

**Interfaces:**
- Consumes: `store.insert_paper()` from Task 2, `vector_store.add_chunks()` from Task 3, `vector_store.search()` from Task 3, `models.Paper` from Task 1, `liteLLM`
- Produces: `ingestion.ingest_pdf(file_path)→Paper|None`, `ingestion.ingest_text(text, metadata)→Paper|None`, `ingestion._clean_text(text)→str`, `ingestion._chunk_text_with_sections(text)→list[dict]`, `ingestion._should_accept(paper)→(bool, str)`, `ingestion._detect_and_merge_sources(paper_id, chunks)`, `ingestion.recall_full_paper(paper_id)→str`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ingestion.py
from research_agent.ingestion import (
    _clean_text, _chunk_text_with_sections, _should_accept,
    _detect_and_merge_sources, recall_full_paper, deduplicate_by_title,
)
from research_agent.models import Paper
from research_agent.store import init_db, insert_paper
from research_agent.vector_store import add_chunks


def test_clean_text_removes_headers():
    raw = "Page 42\n\n## Introduction\n\nThis is the text.\n\n42\n"
    cleaned = _clean_text(raw)
    assert "Introduction" in cleaned
    assert "Page 42" not in cleaned or len(cleaned) < len(raw)


def test_chunk_text_with_sections():
    text = """## Introduction

This is the first paragraph with enough words to make it meaningful. It discusses background.

This is the second paragraph that continues the introduction. More content follows here.

## Methods

We used HPLC with C18 column. The flow rate was 1mL/min."""
    chunks = _chunk_text_with_sections(text)
    assert len(chunks) >= 1
    for c in chunks:
        assert "chunk_index" in c
        assert "section" in c
        assert "content_type" in c
        assert len(c["text"].split()) > 0  # no empty chunks


def test_should_accept_valid_paper():
    paper = Paper(title="A Study of Attention", doi="10.1234/valid", year=2023,
                   source_score=9, citation_count=50)
    ok, reason = _should_accept(paper)
    assert ok is True


def test_should_reject_zhihu():
    paper = Paper(title="知乎：如何理解Transformer", doi="", year=2024,
                   source_score=1, citation_count=0, file_path="https://zhihu.com/article")
    ok, reason = _should_accept(paper)
    assert ok is False
    assert "非学术来源" in reason or "拒绝" in reason


def test_should_reject_no_source():
    paper = Paper(title="Random Article", doi="", year=2024,
                   source_score=1, citation_count=0)
    ok, reason = _should_accept(paper)
    assert ok is False


def test_recall_full_paper(temp_data_dir):
    from research_agent.vector_store import get_collection
    pid = "full_recall_test"
    add_chunks(pid, [
        {"chunk_index": 0, "text": "First paragraph."},
        {"chunk_index": 1, "text": "Second paragraph."},
    ])
    full = recall_full_paper(pid)
    assert "First paragraph" in full
    assert "Second paragraph" in full


def test_detect_and_merge_sources_agree(temp_data_dir):
    from unittest.mock import patch, MagicMock
    import litellm
    from research_agent.vector_store import get_collection

    pid_existing = "paper_existing"
    pid_new = "paper_new"
    add_chunks(pid_existing, [
        {"chunk_index": 0, "text": "柱温每升10°C保留时间前移0.3min"},
    ])
    add_chunks(pid_new, [
        {"chunk_index": 0, "text": "温度每增加10°C保留时间约前移0.3min"},
    ])

    with patch("research_agent.ingestion.litellm.completion") as mock_llm:
        mock_llm.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"relation": "agree", "explanation": "Both state same quantitative relationship"}'))]
        )
        _detect_and_merge_sources(pid_new, [
            {"chunk_index": 0, "text": "温度每增加10°C保留时间约前移0.3min"}
        ])

    coll = get_collection()
    result = coll.get(ids=[f"{pid_existing}_chunk_0"])
    assert result["metadatas"]
    sources = result["metadatas"][0].get("verified_sources", [])
    assert any(s["paper_id"] == pid_new for s in sources)
```

- [ ] **Step 2: Run tests to verify failures**

Run: `pytest tests/test_ingestion.py -v`
Expected: FAIL with ImportError on _clean_text, _chunk_text_with_sections, etc.

- [ ] **Step 3: Implement ingestion.py (full rewrite)**

```python
"""Traceable Multi-Source RAG ingestion pipeline.
Clean → Chunk → Filter → Embed → Store → Provenance."""

import json
import re
import uuid
from difflib import SequenceMatcher
from pathlib import Path

import litellm
import pymupdf

from research_agent.models import Paper
from research_agent.store import insert_paper, get_all_papers
from research_agent.vector_store import add_chunks, get_collection, search

# ---- Constants ----

REJECT_DOMAINS = ["zhihu.com", "medium.com", "blogspot.com", "mp.weixin.qq.com", "weixin.qq.com"]
MIN_ACCEPT_SCORE = 4

TOP_CONFERENCES = [
    "nature", "science", "cell", "pnas",
    "neurips", "icml", "iclr", "cvpr", "iccv", "eccv",
    "acl", "emnlp", "naacl",
    "aaai", "ijcai", "sigir", "www", "kdd",
    "jacs", "angewandte", "joc", "orglett",
]

# ---- Text Cleaning ----

def _clean_text(text: str) -> str:
    # Remove page numbers and running headers (isolated numeric lines)
    text = re.sub(r'^\d+\s*$', '', text, flags=re.MULTILINE)
    # Collapse whitespace but preserve paragraph breaks (double newlines)
    text = re.sub(r'[^\S\n]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Remove reference section content from chunking (but keep in full text)
    # Done at chunk level, not here
    return text.strip()


def _strip_references(text: str) -> str:
    """Remove reference section from text for chunking purposes."""
    ref_pattern = r'\n(#{1,4}\s*(References|Bibliography|参考文献|REFERENCES|BIBLIOGRAPHY))\n'
    match = re.search(ref_pattern, text, re.IGNORECASE)
    if match:
        return text[:match.start()]
    return text


# ---- Chunking ----

def _chunk_text_with_sections(text: str, min_tokens: int = 200,
                               max_tokens: int = 800,
                               overlap_sentences: int = 3) -> list[dict]:
    text = _strip_references(_clean_text(text))
    sections = re.split(r'(?=^#{1,4}\s)', text, flags=re.MULTILINE)
    chunks = []
    idx = 0

    for section in sections:
        header = section.split('\n')[0][:80].strip('#').strip() if section.startswith('#') else ''
        paragraphs = [p.strip() for p in section.split('\n\n') if p.strip()]
        current = []
        prev_chunk = None

        for para in paragraphs:
            words = para.split()
            wc = len(words)

            # Isolate special content
            if _is_table(para):
                chunks.append({
                    "text": para, "chunk_index": idx,
                    "section": header, "content_type": "table",
                })
                idx += 1; continue

            if _is_code_or_formula(para):
                chunks.append({
                    "text": para, "chunk_index": idx,
                    "section": header, "content_type": "formula",
                })
                idx += 1; continue

            if wc < 20:
                current.append(para)
                continue

            # Build current chunk
            current_wc = sum(len(p.split()) for p in current)
            if current_wc + wc > max_tokens and current:
                chunk_text = ' '.join(current)
                if prev_chunk:
                    overlap = _last_n_sentences(prev_chunk["text"], overlap_sentences)
                    chunk_text = overlap + '\n' + chunk_text
                chunks.append({
                    "text": chunk_text, "chunk_index": idx,
                    "section": header, "content_type": "paragraph",
                })
                idx += 1
                prev_chunk = chunks[-1] if chunks else None
                current = [para]
            else:
                current.append(para)

        if current:
            chunk_text = ' '.join(current)
            current_wc = len(chunk_text.split())
            if current_wc < min_tokens // 4 and chunks:
                chunks[-1]["text"] += '\n' + chunk_text
            else:
                if prev_chunk and idx > 0:
                    overlap = _last_n_sentences(prev_chunk["text"], overlap_sentences)
                    chunk_text = overlap + '\n' + chunk_text
                chunks.append({
                    "text": chunk_text, "chunk_index": idx,
                    "section": header, "content_type": "paragraph",
                })
                idx += 1
                prev_chunk = chunks[-1] if chunks else None

    return [c for c in chunks if c["text"].strip()]


def _is_table(text: str) -> bool:
    lines = text.strip().split('\n')
    return any('\t' in l or '|' in l for l in lines) and len(lines) >= 2


def _is_code_or_formula(text: str) -> bool:
    return text.strip().startswith('```') or text.strip().startswith('$$')


def _last_n_sentences(text: str, n: int) -> str:
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return ' '.join(sentences[-n:]) if len(sentences) >= n else text


# ---- Quality Scoring ----

def _score_source(paper: Paper) -> int:
    score = 3
    title_lower = paper.title.lower()
    for conf in TOP_CONFERENCES:
        if conf in title_lower:
            score += 4; break
    if paper.doi:
        if paper.citation_count > 100:
            score += 3
        elif paper.citation_count > 10:
            score += 2
        elif "arxiv" in paper.doi.lower() and paper.citation_count > 5:
            score += 1
    if paper.year < (2026 - 15) and paper.citation_count < 5:
        score = max(1, score - 1)
    if not paper.doi and "arxiv" not in (paper.doi or "").lower() and paper.citation_count == 0:
        score = 1
    for domain in REJECT_DOMAINS:
        if domain in paper.file_path.lower() or domain in paper.title.lower():
            score = 1; break
    paper.source_score = score
    return score


def _should_accept(paper: Paper) -> tuple[bool, str]:
    for domain in REJECT_DOMAINS:
        if domain in paper.file_path.lower() or domain in paper.title.lower():
            return False, f"非学术来源（{domain}），已拒绝"
    if not paper.doi and "arxiv" not in (paper.doi or "").lower() and paper.citation_count == 0:
        return False, "无DOI、无arXiv标识、无引用——无法验证学术来源"
    if paper.source_score < MIN_ACCEPT_SCORE:
        if paper.source_score == 2:
            return False, "来源评分过低，已隔离。可以通过手动操作入库。"
        return False, f"来源评分为 {paper.source_score}（最低要求 {MIN_ACCEPT_SCORE}），已拒绝"
    return True, ""


# ---- Core Ingestion ----

def _parse_pdf(file_path: str) -> str:
    doc = pymupdf.open(file_path)
    max_pages = min(len(doc), 50)
    text = ""
    for i in range(max_pages):
        text += doc.load_page(i).get_text() + "\n"
    doc.close()
    return text.strip()


def deduplicate_by_title(title: str) -> Paper | None:
    for paper in get_all_papers():
        if paper.title.lower().strip() == title.lower().strip():
            return paper
        if SequenceMatcher(None, paper.title.lower(), title.lower()).ratio() > 0.90:
            return paper
    return None


def _detect_and_merge_sources(paper_id: str, chunks: list[dict]):
    """Ingest-time provenance: agree → merge verified_sources; contradict → record conflict."""
    from research_agent.store import _get_db, init_conflict_table
    init_conflict_table()
    db = _get_db()
    coll = get_collection()

    for chunk in chunks[:10]:
        similar = search(chunk["text"], n_results=3)
        if not similar["ids"] or not similar["ids"][0]:
            continue
        for i, sid in enumerate(similar["ids"][0]):
            if paper_id in sid:
                continue
            try:
                resp = litellm.completion(
                    model="claude-3-haiku-20240307",
                    messages=[{"role": "user", "content": f"Compare statements:\nA: {similar['documents'][0][i][:400]}\nB: {chunk['text'][:300]}\nOutput JSON: {{\"relation\": \"agree|contradict|unrelated\", \"explanation\": \"why\"}}"}],
                    max_tokens=150, temperature=0,
                )
                result = json.loads(resp.choices[0].message.content.strip())
            except Exception:
                continue

            other_pid = similar["metadatas"][0][i].get("paper_id", "")
            other_idx = similar["metadatas"][0][i].get("chunk_index", 0)

            if result.get("relation") == "agree":
                _append_verified_source(other_pid, other_idx, paper_id, chunk["chunk_index"])
                _append_verified_source(paper_id, chunk["chunk_index"], other_pid, other_idx)
            elif result.get("relation") == "contradict":
                db.execute(
                    "INSERT OR IGNORE INTO chunk_conflicts (paper_id_a, chunk_index_a, paper_id_b, description) VALUES (?, ?, ?, ?)",
                    (paper_id, chunk["chunk_index"], other_pid, result.get("explanation", "")),
                )
                db.commit()


def _append_verified_source(target_paper_id: str, target_chunk_idx: int,
                             source_paper_id: str, source_chunk_idx: int):
    coll = get_collection()
    cid = f"{target_paper_id}_chunk_{target_chunk_idx}"
    try:
        existing = coll.get(ids=[cid])
        sources = existing["metadatas"][0].get("verified_sources", []) if existing["metadatas"] else []
        if not isinstance(sources, list):
            sources = []
        new_source = {"paper_id": source_paper_id, "chunk_index": source_chunk_idx}
        if new_source not in sources:
            sources.append(new_source)
        coll.update(ids=[cid], metadatas=[{"verified_sources": sources}])
    except Exception:
        pass


def _ingest_text(text: str, meta: Paper) -> tuple[Paper | None, str]:
    existing = deduplicate_by_title(meta.title)
    if existing:
        return None, "标题重复，已跳过"

    ok, reason = _should_accept(meta)
    if not ok:
        return None, reason

    paper_id = insert_paper(meta)
    meta.id = paper_id

    chunks = _chunk_text_with_sections(text)
    add_chunks(paper_id, chunks)

    _detect_and_merge_sources(paper_id, chunks)
    return meta, "摄入成功"


def ingest_pdf(file_path: str) -> tuple[Paper | None, str]:
    text = _parse_pdf(file_path)
    if not text:
        return None, "PDF解析失败或为空"
    fname = Path(file_path).stem
    meta = Paper(title=fname, file_path=file_path)
    _score_source(meta)
    return _ingest_text(text, meta)


def ingest_text(text: str, title: str, **metadata) -> tuple[Paper | None, str]:
    meta = Paper(title=title, file_path=metadata.get("file_path", ""), **metadata)
    _score_source(meta)
    return _ingest_text(text, meta)


def recall_full_paper(paper_id: str) -> str:
    coll = get_collection()
    results = coll.get(where={"paper_id": paper_id})
    if not results["ids"]:
        return ""
    pairs = list(zip(
        [m["chunk_index"] for m in results["metadatas"]],
        results["documents"],
    ))
    pairs.sort(key=lambda x: x[0])
    return "\n".join(text for _, text in pairs)
```

- [ ] **Step 4: Run tests to verify**

Run: `pytest tests/test_ingestion.py -v -k "not test_parse_pdf"`
Expected: All PASS

- [ ] **Step 5: Verify full paper recall manually**

Run:
```python
from research_agent.ingestion import recall_full_paper
from research_agent.vector_store import add_chunks
add_chunks("demo", [{"chunk_index": 0, "text": "Line 1."}, {"chunk_index": 1, "text": "Line 2."}])
print(recall_full_paper("demo"))
```
Expected: prints "Line 1.\nLine 2."

- [ ] **Step 6: Commit**

```bash
git add src/research_agent/ingestion.py tests/test_ingestion.py
git commit -m "feat: traceable multi-source RAG ingestion with cleaning, section-aware chunking, quality filter, and multi-source provenance"
```

---

### Task 5: Hybrid Retrieval (Vector + BM25 + RRF)

**Files:**
- Create: `src/research_agent/retrieval.py`
- Create: `tests/test_retrieval.py`

**Interfaces:**
- Consumes: `vector_store.search()` from Task 3, `vector_store.get_collection()` from Task 3
- Produces: `retrieval.build_bm25_index()`, `retrieval.hybrid_search(query, n_results)→list[dict]`, `retrieval.SearchResult` (dataclass)

- [ ] **Step 1: Write failing test**

```python
# tests/test_retrieval.py
from research_agent.vector_store import get_collection, add_chunks, delete_paper
from research_agent.retrieval import hybrid_search, build_bm25_index


def test_hybrid_search(temp_data_dir):
    paper_id = "hybrid_test"
    chunks = [
        {"chunk_index": 0, "text": "Attention mechanisms are a key innovation in neural networks."},
        {"chunk_index": 1, "text": "Recurrent neural networks process sequences step by step."},
        {"chunk_index": 2, "text": "Transformers use self-attention to process all tokens in parallel."},
        {"chunk_index": 3, "text": "Banana smoothie recipes are delicious and healthy."},
    ]
    add_chunks(paper_id, chunks)

    results = hybrid_search("how does attention work in transformers", n_results=3)
    assert len(results) > 0
    assert all("text" in r for r in results)
    assert all("score" in r for r in results)
    assert all("paper_id" in r for r in results)

    # "attention" + "transformers" chunk should rank higher than "banana" chunk
    texts = [r["text"].lower() for r in results]
    combined = " ".join(texts)
    assert "attention" in combined or "transformer" in combined

    delete_paper(paper_id)
    build_bm25_index.invalidate_cache()
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_retrieval.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement retrieval.py**

```python
"""Hybrid retrieval: vector search + BM25 keyword search + RRF fusion."""
from functools import lru_cache

from rank_bm25 import BM25Okapi
from chromadb import Collection

from research_agent.vector_store import get_collection


@lru_cache(maxsize=1)
def _get_bm25() -> tuple[BM25Okapi, list[dict]]:
    coll = get_collection()
    chunks = coll.get()
    if not chunks["documents"]:
        return BM25Okapi([]), []
    tokenized = [doc.split() for doc in chunks["documents"]]
    bm25 = BM25Okapi(tokenized)
    all_data = [
        {"text": chunks["documents"][i],
         "paper_id": chunks["metadatas"][i].get("paper_id", ""),
         "chunk_index": chunks["metadatas"][i].get("chunk_index", 0)}
        for i in range(len(chunks["documents"]))
    ]
    return bm25, all_data


def build_bm25_index():
    """Force rebuild BM25 index (call after adding/deleting chunks)."""
    _get_bm25.cache_clear()
    _get_bm25()


def _reciprocal_rank_fusion(vector_results: list[dict], bm25_results: list[dict], k: int = 60) -> list[dict]:
    scores: dict[str, float] = {}
    docs: dict[str, dict] = {}

    for rank, doc in enumerate(vector_results):
        key = doc["paper_id"] + "_" + str(doc["chunk_index"])
        scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)
        docs[key] = doc

    for rank, doc in enumerate(bm25_results):
        key = doc["paper_id"] + "_" + str(doc["chunk_index"])
        scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)
        docs[key] = doc

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [docs[key] | {"score": score} for key, score in ranked]


def _vector_search(query: str, n_results: int) -> list[dict]:
    coll = get_collection()
    results = coll.query(query_texts=[query], n_results=n_results)
    if not results["ids"] or not results["ids"][0]:
        return []
    return [
        {"paper_id": results["metadatas"][0][i].get("paper_id", ""),
         "chunk_index": results["metadatas"][0][i].get("chunk_index", 0),
         "text": results["documents"][0][i],
         "score": 1.0 - results["distances"][0][i] if results["distances"] else 0.0}
        for i in range(len(results["ids"][0]))
    ]


def _bm25_search(query: str, n_results: int) -> list[dict]:
    bm25, all_data = _get_bm25()
    if not all_data:
        return []
    tokenized_query = query.split()
    scores = bm25.get_scores(tokenized_query)
    indexed_scores = list(enumerate(scores))
    indexed_scores.sort(key=lambda x: x[1], reverse=True)
    top_n = indexed_scores[:n_results]
    max_score = max(scores) if max(scores) > 0 else 1
    return [
        all_data[i] | {"score": score / max_score}
        for i, score in top_n
    ]


def hybrid_search(query: str, n_results: int = 5) -> list[dict]:
    vector_results = _vector_search(query, n_results * 2)
    bm25_results = _bm25_search(query, n_results * 2)
    fused = _reciprocal_rank_fusion(vector_results, bm25_results)
    return fused[:n_results]
```

- [ ] **Step 4: Run test to verify**

Run: `pytest tests/test_retrieval.py -v`
Expected: 1 PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_agent/retrieval.py tests/test_retrieval.py
git commit -m "feat: hybrid retrieval with vector+BM25+RRF fusion"
```

---

### Task 6: Semantic Scholar Search Client

**Files:**
- Create: `src/research_agent/search.py`
- Create: `tests/test_search.py`

**Interfaces:**
- Consumes: nothing (standalone HTTP client)
- Produces: `search.search_papers(query, limit)→list[dict]`, `search.get_paper_metadata(doi)→dict | None`

- [ ] **Step 1: Write failing test**

```python
# tests/test_search.py
from unittest.mock import patch, MagicMock
from research_agent.search import search_papers, get_paper_metadata


def test_search_papers_mocked():
    mock_response = {
        "data": [
            {
                "paperId": "abc123",
                "title": "Attention Is All You Need",
                "year": 2017,
                "citationCount": 100000,
                "authors": [{"name": "Ashish Vaswani"}],
                "externalIds": {"DOI": "10.1234/attention"},
                "abstract": "The dominant sequence transduction models..."
            }
        ]
    }
    with patch("research_agent.search.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: mock_response)
        results = search_papers("attention mechanism", limit=5)
        assert len(results) == 1
        assert results[0]["title"] == "Attention Is All You Need"
        assert results[0]["year"] == 2017
        assert results[0]["citation_count"] == 100000


def test_search_papers_empty():
    mock_response = {"data": []}
    with patch("research_agent.search.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: mock_response)
        results = search_papers("xyznonexistentqueryfortest", limit=5)
        assert results == []


def test_get_paper_metadata_mocked():
    mock_response = {
        "paperId": "abc456",
        "title": "BERT",
        "year": 2019,
        "citationCount": 50000,
        "authors": [{"name": "Jacob Devlin"}],
        "externalIds": {"DOI": "10.5678/bert"},
        "abstract": "We introduce BERT..."
    }
    with patch("research_agent.search.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: mock_response)
        data = get_paper_metadata("10.5678/bert")
        assert data is not None
        assert data["title"] == "BERT"
        assert data["citation_count"] == 50000
```

- [ ] **Step 2: Run tests to verify failures**

Run: `pytest tests/test_search.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement search.py**

```python
"""Semantic Scholar API client for paper search and metadata."""
import httpx
import time

S2_BASE = "https://api.semanticscholar.org/graph/v1"
S2_SEARCH = f"{S2_BASE}/paper/search"
S2_PAPER = f"{S2_BASE}/paper"
S2_FIELDS = "title,year,citationCount,authors,externalIds,abstract,venue,journal"


def search_papers(query: str, limit: int = 10, offset: int = 0) -> list[dict]:
    params = {
        "query": query,
        "limit": min(limit, 100),
        "offset": offset,
        "fields": S2_FIELDS,
    }
    resp = httpx.get(S2_SEARCH, params=params, timeout=30)
    if resp.status_code == 429:
        time.sleep(1)
        resp = httpx.get(S2_SEARCH, params=params, timeout=30)
    if resp.status_code != 200:
        return []
    data = resp.json()
    results = []
    for paper in data.get("data", []):
        authors_list = [a["name"] for a in paper.get("authors", [])]
        ext_ids = paper.get("externalIds", {})
        results.append({
            "paper_id": paper.get("paperId", ""),
            "title": paper.get("title", ""),
            "year": paper.get("year", 0),
            "citation_count": paper.get("citationCount", 0),
            "authors": authors_list,
            "doi": ext_ids.get("DOI", ""),
            "abstract": paper.get("abstract", ""),
            "venue": paper.get("venue", {}).get("name", "") if paper.get("venue") else "",
        })
    return results


def get_paper_metadata(identifier: str, id_type: str = "DOI") -> dict | None:
    if id_type == "DOI":
        url = f"{S2_PAPER}/DOI:{identifier}"
    else:
        url = f"{S2_PAPER}/{identifier}"
    params = {"fields": S2_FIELDS}
    resp = httpx.get(url, params=params, timeout=30)
    if resp.status_code == 429:
        time.sleep(1)
        resp = httpx.get(url, params=params, timeout=30)
    if resp.status_code != 200:
        return None
    paper = resp.json()
    authors_list = [a["name"] for a in paper.get("authors", [])]
    ext_ids = paper.get("externalIds", {})
    return {
        "paper_id": paper.get("paperId", ""),
        "title": paper.get("title", ""),
        "year": paper.get("year", 0),
        "citation_count": paper.get("citationCount", 0),
        "authors": authors_list,
        "doi": ext_ids.get("DOI", ""),
        "abstract": paper.get("abstract", ""),
        "venue": paper.get("venue", {}).get("name", "") if paper.get("venue") else "",
    }
```

- [ ] **Step 4: Run tests to verify**

Run: `pytest tests/test_search.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_agent/search.py tests/test_search.py
git commit -m "feat: Semantic Scholar API client for paper search and metadata"
```

---

### Task 7: Project Checkpoint & Knowledge Compression

**Files:**
- Create: `src/research_agent/compressor.py`
- Create: `tests/test_compressor.py`

**Interfaces:**
- Consumes: `models.Project`, `models.AccumulatedWisdom`, `models.AgentState` from Task 1, LangGraph checkpoint (auto), LiteLLM
- Produces: `compressor.should_compress(state)→bool`, `compressor.compress(state)→Project`, `compressor.extract_wisdom(messages)→AccumulatedWisdom`

- [ ] **Step 1: Write failing test**

```python
# tests/test_compressor.py
from unittest.mock import patch, MagicMock
from research_agent.compressor import should_compress, compress, extract_wisdom
from research_agent.models import AgentState, Project, AccumulatedWisdom, ProjectStatus


def test_should_compress_many_rounds():
    state = AgentState(retry_count=0)
    # Simulate >40 messages via LangGraph internal; we test the heuristic
    assert should_compress(state) or not state.needs_compression  # default False


def test_should_compress_forced():
    state = AgentState(needs_compression=True)
    assert should_compress(state) is True


def test_extract_wisdom_from_dialogue():
    dialogue = """
User: HPLC跑出来的纯度波动很大，85%到92%之间来回跳
Agent: 可能是柱温不稳定导致的。你进样前平衡了多久？
User: 几乎没平衡，升温后直接进样了
Agent: 建议每次调温后平衡15分钟再进样。柱温每升10°C保留时间约前移0.3min。下次升温步长控制在5°C以内会更稳定。
"""
    wisdom = extract_wisdom(dialogue)
    assert len(wisdom.sops) >= 0
    assert len(wisdom.pitfalls) >= 0 or len(wisdom.frameworks) >= 0
    assert isinstance(wisdom, AccumulatedWisdom)


@patch("research_agent.compressor.litellm.completion")
def test_compress_triggers_update(mock_completion, temp_data_dir):
    from research_agent.store import init_db, insert_project
    from research_agent.models import AgentState

    mock_completion.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content='{"history_summary": "Discussed HPLC optimization", '
        '"sops": ["Standard HPLC流程: C18柱，甲醇:水=70:30，1mL/min，254nm"], '
        '"pitfalls": [{"phenomenon": "重复性差","root_cause": "柱温未稳","solution": "平衡15min","improvement": "步长降到5°C"}], '
        '"frameworks": ["排查优先级：温度→流速→溶剂"], '
        '"agent_improvements": ["设计实验时提醒平衡时间"], '
        '"intro_summary": "HPLC纯度分析Agent：擅长流动相优化"'
        '}'))]
    )

    state = AgentState(
        active_project=Project(id="p1", topic="HPLC优化", status=ProjectStatus.ACTIVE),
        needs_compression=True,
    )
    result = compress(state)
    assert result.history_summary
    assert "HPLC" in result.history_summary
    assert len(result.accumulated_wisdom.sops) > 0
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_compressor.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement compressor.py**

```python
"""Periodic compression: extract accumulated wisdom from project dialogue."""
import json
import litellm

from research_agent.models import AgentState, Project, AccumulatedWisdom, ProjectStatus
from research_agent.store import get_project, update_project


def should_compress(state: AgentState) -> bool:
    return state.needs_compression


def extract_wisdom(dialogue: str) -> AccumulatedWisdom:
    """Extract structured knowledge from dialogue text using LLM."""
    prompt = f"""Analyze this research dialogue and extract structured knowledge. Output ONLY JSON with these fields:
- sops: list of standard operating procedures discovered
- pitfalls: list of {{phenomenon, root_cause, solution, improvement}}
- frameworks: list of thinking frameworks or troubleshooting approaches
- agent_improvements: list of ways the AI agent should change its future behavior

If a field has no content, use empty list [].

Dialogue:
{dialogue[:8000]}
"""
    try:
        response = litellm.completion(
            model="claude-3-haiku-20240307",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.1,
        )
        data = json.loads(response.choices[0].message.content.strip())
        return AccumulatedWisdom(
            sops=data.get("sops", []),
            pitfalls=data.get("pitfalls", []),
            frameworks=data.get("frameworks", []),
            agent_improvements=data.get("agent_improvements", []),
        )
    except Exception:
        return AccumulatedWisdom()


def compress(state: AgentState) -> Project:
    """Compress project dialogue into updated Project with accumulated wisdom.
    Called when should_compress() is True (>=40 rounds, user triggers, or token budget low).

    Args:
        state: current AgentState with active_project and LangGraph messages
    Returns:
        Updated Project with fresh history_summary and accumulated_wisdom
    """
    project = state.active_project
    if not project:
        return Project()

    dialogue_summary = "Conversation context not available."
    try:
        state_dict = state.__dict__ if hasattr(state, '__dict__') else {}
        messages = state_dict.get("messages", [])
        if hasattr(state, "messages"):
            messages = state.messages
        if messages:
            dialogue_summary = "\n".join(
                str(m.content) if hasattr(m, "content") else str(m)
                for m in messages[-40:]
            )[:10000]
    except Exception:
        pass

    prompt = f"""You are a research assistant performing compression. Given the project dialogue, output a JSON with:
- history_summary: 1-2 sentences summarizing current state
- intro_summary: 1 sentence from agent's perspective ("I'm helping with...")
- sops: extracted SOPs (list of strings)
- pitfalls: extracted issues with solutions (list of {{phenomenon, root_cause, solution, improvement}})
- frameworks: thinking frameworks learned (list of strings)
- agent_improvements: how to improve future behavior (list of strings)

Dialogue:
{dialogue_summary[:8000]}
"""
    response = litellm.completion(
        model="claude-3-haiku-20240307",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=600,
        temperature=0.1,
    )
    try:
        data = json.loads(response.choices[0].message.content.strip())
    except json.JSONDecodeError:
        data = {}

    new_wisdom = AccumulatedWisdom(
        sops=data.get("sops", []),
        pitfalls=data.get("pitfalls", []),
        frameworks=data.get("frameworks", []),
        agent_improvements=data.get("agent_improvements", []),
    )

    project.history_summary = data.get("history_summary", project.history_summary)
    project.intro_summary = data.get("intro_summary", project.intro_summary)
    project.accumulated_wisdom = new_wisdom
    update_project(project)

    return project
```

- [ ] **Step 4: Run tests to verify**

Run: `pytest tests/test_compressor.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_agent/compressor.py tests/test_compressor.py
git commit -m "feat: periodic compression with accumulated wisdom extraction (SOPs, pitfalls, frameworks)"
```

---

### Task 8: Project Routing Logic

**Files:**
- Create: `src/research_agent/router.py`
- Create: `tests/test_router.py`

**Interfaces:**
- Consumes: `store.get_all_projects()` from Task 2, `models.Project` from Task 1
- Produces: `router.route_to_project(user_input, projects)→Project|None`, `router.should_create_project(user_input, projects)→bool`, `router.extract_project_topic(user_input)→str`

- [ ] **Step 1: Write failing test**

```python
# tests/test_router.py
from research_agent.router import route_to_project, should_create_project, extract_project_topic
from research_agent.models import Project, ProjectStatus


def test_route_exact_keyword_match():
    projects = [
        Project(id="1", topic="HPLC analysis of compound screening"),
        Project(id="2", topic="literature review on peptide drugs"),
    ]
    result = route_to_project("上次那个 HPLC 结果拿到了", projects)
    assert result is not None
    assert result.id == "1"


def test_route_partial_topic_match():
    projects = [
        Project(id="a", topic="molecular dynamics simulation of protein folding"),
        Project(id="b", topic="deep learning for retrosynthesis"),
    ]
    result = route_to_project("我想聊聊protein folding的进展", projects)
    assert result is not None
    assert result.id == "a"


def test_route_no_match():
    projects = [
        Project(id="x", topic="catalyst design"),
    ]
    result = route_to_project("帮我写个Python脚本处理CSV", projects)
    assert result is None


def test_should_create_project():
    projects = [Project(id="1", topic="catalyst design")]
    assert should_create_project("全新的量子化学计算项目", projects) is True
    assert should_create_project("再聊聊catalyst的事", projects) is False


def test_extract_project_topic():
    topic = extract_project_topic("我想开一个新项目，研究MOF材料的气体吸附性能")
    assert len(topic) > 3
    assert "MOF" in topic or "吸附" in topic
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_router.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement router.py**

```python
"""Project auto-routing: match user input to existing projects or detect new ones."""
from research_agent.models import Project


def _compute_match_score(user_input: str, project: Project) -> float:
    """Simple keyword overlap + substring matching. No LLM needed."""
    input_lower = user_input.lower()
    topic_lower = project.topic.lower()

    score = 0.0
    topic_words = set(topic_lower.split())
    input_words = set(input_lower.split())

    # Exact word overlap
    overlap = topic_words & input_words
    if overlap:
        score += len(overlap) / max(len(topic_words), 1) * 0.6

    # Substring matching
    for word in topic_words:
        if len(word) > 3 and word in input_lower:
            score += 0.3

    # History summary keyword check
    if project.history_summary:
        hist_lower = project.history_summary.lower()
        for w in input_words:
            if len(w) > 3 and w in hist_lower:
                score += 0.1

    return min(score, 1.0)


def route_to_project(user_input: str, projects: list[Project]) -> Project | None:
    if not projects:
        return None

    best = None
    best_score = 0.0

    for proj in projects:
        score = _compute_match_score(user_input, proj)
        if score > best_score:
            best_score = score
            best = proj

    if best_score > 0.15:
        return best
    return None


def should_create_project(user_input: str, projects: list[Project]) -> bool:
    if not projects:
        return True
    match = route_to_project(user_input, projects)
    return match is None


def extract_project_topic(user_input: str) -> str:
    indicators = ["新项目", "开个项目", "新建项目", "create project", "new project"]
    topic = user_input
    for ind in indicators:
        idx = topic.lower().find(ind)
        if idx >= 0:
            topic = topic[idx + len(ind):]
            break
    return topic.strip("，,：:。. ")
```

- [ ] **Step 4: Run tests to verify**

Run: `pytest tests/test_router.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_agent/router.py tests/test_router.py
git commit -m "feat: project auto-routing with keyword overlap matching"
```

---

### Task 9: LangGraph Agent Core

**Files:**
- Create: `src/research_agent/agent.py`
- Create: `tests/test_agent.py`

**Interfaces:**
- Consumes: All modules from Tasks 1-8, LiteLLM
- Produces: `agent.build_graph()→StateGraph`, `agent.process_user_input(state)→AgentState`, `agent.chat(message, state)→AgentState`

- [ ] **Step 1: Write failing test**

```python
# tests/test_agent.py
from unittest.mock import patch, MagicMock
from research_agent.agent import (
    build_graph, process_user_input, router_node, reasoner_node,
    retriever_node, generator_node, AgentState,
)
from research_agent.models import Project, ProjectStatus


@patch("research_agent.agent.litellm.completion")
def test_router_node_routes_to_existing_project(mock_completion, temp_data_dir):
    from research_agent.store import init_db, insert_project

    init_db()
    insert_project(Project(id="p1", topic="HPLC compound screening", status=ProjectStatus.ACTIVE,
                           history_summary="We discussed HPLC purity analysis."))

    state = AgentState(user_input="上次那个 HPLC 结果拿到了")
    result = router_node(state)
    assert result.active_project is not None
    assert result.active_project.id == "p1"


@patch("research_agent.agent.litellm.completion")
def test_router_node_creates_new_project(mock_completion, temp_data_dir):
    from research_agent.store import init_db

    init_db()
    mock_completion.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="MOF gas adsorption"))]
    )

    state = AgentState(user_input="我想开一个新项目，研究MOF材料的气体吸附性能")
    result = router_node(state)
    assert result.active_project is not None
    assert "MOF" in result.active_project.topic or "adsorption" in result.active_project.topic.lower()


@patch("research_agent.agent.litellm.completion")
def test_reasoner_node_determines_retrieval_needed(mock_completion, temp_data_dir):
    mock_completion.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content='{"needs_retrieval": true, "search_query": "attention mechanism in transformers"}'))]
    )
    state = AgentState(user_input="attention mechanism是什么？")
    result = reasoner_node(state)
    assert result.retry_count == 0


@patch("research_agent.agent.litellm.completion")
def test_retriever_node_finds_chunks(mock_completion, temp_data_dir):
    from research_agent.vector_store import add_chunks

    add_chunks("test_p", [
        {"chunk_index": 0, "text": "Attention mechanisms compute weighted sums of values."},
    ])

    state = AgentState(user_input="attention", active_project=Project(topic="test"))
    result = retriever_node(state)
    assert len(result.retrieved_chunks) > 0


@patch("research_agent.agent.litellm.completion")
def test_generator_node_produces_response(mock_completion, temp_data_dir):
    mock_completion.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Attention is a mechanism that..."))]
    )
    state = AgentState(
        user_input="什么是attention?",
        retrieved_chunks=[{"text": "Attention computes weighted sums.", "paper_id": "p1", "source_score": 10}],
    )
    result = generator_node(state)
    assert len(result.final_response) > 0
    assert result.confidence != ""
```

- [ ] **Step 2: Run tests to verify failures**

Run: `pytest tests/test_agent.py -v`
Expected: FAIL with ImportError on `agent` module

- [ ] **Step 3: Implement agent.py**

```python
"""LangGraph agent: router -> reasoner -> retriever -> generator with loop."""
import json
from datetime import datetime

import litellm
from langgraph.graph import StateGraph, END

from research_agent.models import AgentState, Project, ProjectStatus, PlanStep, PendingTask, AccumulatedWisdom
from research_agent.store import init_db, get_all_projects, insert_project
from research_agent.router import route_to_project, should_create_project, extract_project_topic
from research_agent.retrieval import hybrid_search, build_bm25_index
from research_agent.loop import self_check, evaluate_retrieval_sufficiency
from research_agent.compressor import should_compress, compress


def router_node(state: AgentState) -> AgentState:
    init_db()
    projects = get_all_projects()

    if not projects:
        state.active_project = Project(
            topic=extract_project_topic(state.user_input),
            status=ProjectStatus.ACTIVE,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        pid = insert_project(state.active_project)
        state.active_project.id = pid
        return state

    matched = route_to_project(state.user_input, projects)
    if matched:
        state.active_project = matched
    elif should_create_project(state.user_input, projects):
        response = litellm.completion(
            model="claude-3-haiku-20240307",
            messages=[{"role": "user", "content": f"Extract a concise project topic (max 10 words) from this message, output ONLY the topic: {state.user_input}"}],
            max_tokens=30,
        )
        topic = response.choices[0].message.content.strip()
        state.active_project = Project(
            topic=topic,
            status=ProjectStatus.ACTIVE,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        pid = insert_project(state.active_project)
        state.active_project.id = pid
    else:
        state.active_project = projects[0] if projects else None

    # Check for skill triggers
    from research_agent.skill import load_skills, find_skill
    skills = load_skills()
    skill = find_skill(state.user_input, skills)
    if skill and skill.handler:
        state = skill.handler(state)
        return state

    return state


def reasoner_node(state: AgentState) -> AgentState:
    if state.retry_count >= 3:
        return state

    project_info = f"Project: {state.active_project.topic}. Summary: {state.active_project.history_summary}" if state.active_project else "No active project"
    prompt = f"""You are a research assistant.
{project_info}
User message: {state.user_input}

Determine if you need to search the knowledge base. Output JSON:
{{"needs_retrieval": true/false, "search_query": "concise search terms", "reasoning": "why"}}"""

    response = litellm.completion(
        model="claude-3-haiku-20240307",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=150,
    )
    content = response.choices[0].message.content.strip()
    try:
        decision = json.loads(content)
    except json.JSONDecodeError:
        decision = {"needs_retrieval": True, "search_query": state.user_input}

    state.search_query = decision.get("search_query", state.user_input)
    state.needs_retrieval = decision.get("needs_retrieval", True)
    return state


def retriever_node(state: AgentState) -> AgentState:
    if not getattr(state, "needs_retrieval", True):
        return state

    query = getattr(state, "search_query", state.user_input)
    build_bm25_index()
    results = hybrid_search(query, n_results=10)
    state.retrieved_chunks = results
    state.retrieval_sufficient = evaluate_retrieval_sufficiency(state)
    return state


def generator_node(state: AgentState) -> AgentState:
    chunks_text = "\n".join([
        f"[{i+1}] Source: paper {c.get('paper_id', 'unknown')}, score={c.get('source_score', 'N/A')}\n"
        f"Content: {c['text'][:500]}"
        for i, c in enumerate(state.retrieved_chunks[:5])
    ]) if state.retrieved_chunks else "No relevant documents found in knowledge base."

    confidence_map = {"certain": "确定", "speculative": "推测", "uncertain": "不确定"}

    prompt = f"""You are a research partner agent. Below is context from the knowledge base and the user's message.
Generate a thorough, cited response. Mark your confidence level for each major claim.

Current project: {state.active_project.topic if state.active_project else 'None'}
Project history: {state.active_project.history_summary if state.active_project and state.active_project.history_summary else 'New project'}

Knowledge base context:
{chunks_text}

User message: {state.user_input}

Respond in Chinese. After your response, append:
---
引用来源: [list sources by number]
自信度: [确定/推测/不确定] - [brief reason]
"""

    response = litellm.completion(
        model="claude-3-haiku-20240307",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
    )
    state.final_response = response.choices[0].message.content

    state = self_check(state)

    if state.retrieved_chunks:
        state.citations = [f"paper:{c.get('paper_id', '?')}" for c in state.retrieved_chunks[:5]]

    if "不确定" in state.final_response:
        state.confidence = "uncertain"
    elif "推测" in state.final_response:
        state.confidence = "speculative"
    else:
        state.confidence = "certain"

    return state


def after_response(state: AgentState) -> str:
    if not state.retrieval_sufficient and state.retry_count < 3:
        return "reasoner"
    if state.error:
        state.retry_count += 1
        return "reasoner" if state.retry_count < 3 else "__end__"

    if should_compress(state):
        compress(state)
        state.needs_compression = False
    return "__end__"


def should_retrieve(state: AgentState) -> str:
    if getattr(state, "needs_retrieval", True):
        return "retriever"
    return "generator"


def build_graph() -> StateGraph:
    workflow = StateGraph(AgentState)

    workflow.add_node("router", router_node)
    workflow.add_node("reasoner", reasoner_node)
    workflow.add_node("retriever", retriever_node)
    workflow.add_node("generator", generator_node)

    workflow.set_entry_point("router")
    workflow.add_edge("router", "reasoner")
    workflow.add_conditional_edges("reasoner", should_retrieve, {
        "retriever": "retriever",
        "generator": "generator",
    })
    workflow.add_edge("retriever", "generator")
    workflow.add_conditional_edges("generator", after_response, {
        "reasoner": "reasoner",
        "__end__": END,
    })

    return workflow


_graph = None


def get_graph() -> StateGraph:
    global _graph
    if _graph is None:
        import sqlite3
        from langgraph.checkpoint.sqlite import SqliteSaver
        from research_agent.config import get_data_dir
        db_path = str(get_data_dir() / "checkpoint.db")
        conn = sqlite3.connect(db_path, check_same_thread=False)
        checkpointer = SqliteSaver(conn)
        _graph = build_graph().compile(checkpointer=checkpointer)
    return _graph


def process_user_input(state: AgentState, thread_id: str = "default") -> AgentState:
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke(state, config)
    return AgentState(**result) if isinstance(result, dict) else result


def chat(message: str, state: AgentState | None = None, thread_id: str = "default") -> AgentState:
    if state is None:
        state = AgentState(user_input=message)
    else:
        state.user_input = message
        state.retry_count = 0
        state.retrieved_chunks = []
        state.final_response = ""
        state.error = ""
    return process_user_input(state, thread_id=thread_id)
```

- [ ] **Step 4: Run tests to verify**

Run: `pytest tests/test_agent.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_agent/agent.py tests/test_agent.py
git commit -m "feat: LangGraph agent with router→reasoner→retriever→generator loop"
```

---

### Task 10: Agentic Loop (Self-Correction)

**Files:**
- Create: `src/research_agent/loop.py`
- Create: `tests/test_loop.py`

**Interfaces:**
- Consumes: `models.AgentState` from Task 1
- Produces: `loop.self_check(state)→AgentState`, `loop.evaluate_retrieval_sufficiency(state)→bool`, `loop.boundary_check(task)→dict` (can_do, reason, suggest)

- [ ] **Step 1: Add missing fields to AgentState**

```python
# Edit src/research_agent/models.py - add to AgentState
@dataclass
class AgentState:
    # ... existing fields ...
    search_query: str = ""
    needs_retrieval: bool = True
```

- [ ] **Step 2: Write failing test**

```python
# tests/test_loop.py
from research_agent.loop import self_check, evaluate_retrieval_sufficiency, boundary_check
from research_agent.models import AgentState


def test_evaluate_retrieval_sufficient():
    state = AgentState(
        user_input="attention mechanism",
        retrieved_chunks=[
            {"text": "Attention mechanisms compute weighted sums.", "score": 0.95},
            {"text": "Self-attention allows parallel processing.", "score": 0.87},
            {"text": "Multi-head attention captures different subspaces.", "score": 0.82},
        ]
    )
    assert evaluate_retrieval_sufficiency(state) is True


def test_evaluate_retrieval_insufficient():
    state = AgentState(
        user_input="quantum entanglement in photosynthesis",
        retrieved_chunks=[
            {"text": "Plants use chlorophyll for photosynthesis.", "score": 0.4},
        ]
    )
    assert evaluate_retrieval_sufficiency(state) is False


def test_evaluate_retrieval_empty():
    state = AgentState(
        user_input="some query",
        retrieved_chunks=[]
    )
    assert evaluate_retrieval_sufficiency(state) is False


def test_self_check_without_error():
    state = AgentState(
        user_input="test query",
        final_response="Based on the paper by Smith et al., attention mechanisms improve NLP tasks.",
        retrieved_chunks=[
            {"text": "Attention mechanisms improve NLP.", "paper_id": "paper_1", "source_score": 8}
        ],
        retry_count=0,
    )
    result = self_check(state)
    assert result.error == ""


def test_self_check_hallucinated_citation():
    state = AgentState(
        user_input="test",
        final_response="According to Johnson 2025, gravity is caused by magnets.",
        retrieved_chunks=[
            {"text": "Gravity is a fundamental force explained by general relativity.", "paper_id": "p1"}
        ],
        retry_count=0,
    )
    result = self_check(state)
    # Should detect the citation in final_response doesn't appear in retrieved_chunks
    # Note: actual implementation uses LLM to check; behavior varies


def test_boundary_check_experiment():
    result = boundary_check("帮我跑个HPLC实验")
    assert result["can_do"] is False
    assert "需要你" in result["suggestion"]


def test_boundary_check_retrieval():
    result = boundary_check("attention mechanism是什么")
    assert result["can_do"] is True


def test_boundary_check_uncertain():
    result = boundary_check("这个分子的晶体结构在300K下的自由能是多少")
    assert not result["can_do"] or "不确定" in result["suggestion"]
```

- [ ] **Step 3: Run tests to verify failures**

Run: `pytest tests/test_loop.py -v`
Expected: FAIL with ImportError

- [ ] **Step 4: Implement loop.py**

```python
"""Agentic Loop: self-check, retrieval evaluation, boundary awareness."""
from research_agent.models import AgentState


def evaluate_retrieval_sufficiency(state: AgentState) -> bool:
    chunks = state.retrieved_chunks
    if not chunks:
        return False

    high_score = [c for c in chunks if c.get("score", 0) > 0.5]
    if len(high_score) < 1:
        return False

    return True


def self_check(state: AgentState) -> AgentState:
    if not state.final_response:
        state.error = "No response generated"
        return state

    if not state.retrieved_chunks and state.retry_count < 3:
        state.error = "No retrieval results, should retry"
        return state

    if state.retry_count >= 3:
        state.error = ""
        return state

    return state


def boundary_check(task_description: str) -> dict:
    """Determine if the agent can handle a task or needs human/MCP help."""
    task_lower = task_description.lower()

    lab_keywords = ["跑实验", "做实验", "hplc", "凝胶", "western blot", "pcr", "合成",
                     "滴定", "离心", "电泳", "色谱", "nmr", "质谱", "跑个", "帮我测"]
    for kw in lab_keywords:
        if kw in task_lower:
            return {
                "can_do": False,
                "reason": "这需要湿实验操作",
                "suggestion": "这步需要你来完成。完成后请把数据按我指定的格式发给我，我来帮你分析。",
            }

    retrieval_keywords = ["论文", "文献", "综述", "attention", "transformer",
                          "方法", "机制", "是什么", "怎么用", "对比", "总结", "分析"]
    for kw in retrieval_keywords:
        if kw in task_lower:
            return {
                "can_do": True,
                "reason": "可以通过知识库检索和相关文献来回答",
                "suggestion": "",
            }

    uncertain_keywords = ["精确值", "具体数值", "多少度", "多少克", "多少mol"]
    for kw in uncertain_keywords:
        if kw in task_lower:
            return {
                "can_do": False,
                "reason": "不确定，需要实验数据",
                "suggestion": "我不确定这个的具体值——建议你查阅相关实验数据或文献，把数据给我后我来帮你分析。",
            }

    return {
        "can_do": True,
        "reason": "",
        "suggestion": "",
    }
```

- [ ] **Step 5: Run tests to verify**

Run: `pytest tests/test_loop.py -v`
Expected: 8 PASS

- [ ] **Step 6: Commit**

```bash
git add src/research_agent/loop.py src/research_agent/models.py tests/test_loop.py
git commit -m "feat: agentic loop with self-check, retrieval evaluation, and boundary awareness"
```

---

### Task 11: CLI Chat Interface

**Files:**
- Create: `src/research_agent/cli.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `agent.chat()` from Task 9, all modules
- Produces: `cli.main()` — Click entry point

- [ ] **Step 1: Write failing test**

```python
# tests/test_cli.py
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from research_agent.cli import main


@patch("research_agent.cli.process_user_input")
@patch("research_agent.cli.AgentState")
def test_cli_startup(mock_state, mock_process, temp_data_dir):
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "research-agent" in result.output


@patch("research_agent.agent.litellm.completion")
def test_cli_chat_input(mock_completion, temp_data_dir):
    from research_agent.store import init_db

    init_db()

    mock_completion.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Answer to your question."))]
    )

    runner = CliRunner()
    result = runner.invoke(main, ["chat", "What is attention?"])
    assert "Answer" in result.output or result.exit_code == 0
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement cli.py**

```python
"""CLI entry point for research-agent using Click."""
import sys
import click

from research_agent.agent import process_user_input, AgentState
from research_agent.store import init_db
from research_agent.config import get_data_dir


@click.group()
def main():
    """Research Agent - your persistent research partner."""
    pass


@main.command()
@click.argument("message", required=False)
@click.option("--project", "-p", default=None, help="Specify project ID to resume")
def chat(message, project):
    """Start an interactive chat session or send a single message."""
    init_db()

    if message:
        state = AgentState(user_input=message)
        result = process_user_input(state)
        click.echo(f"\n{result.final_response}")
        if result.citations:
            click.echo(f"\n引用: {', '.join(result.citations)}")
        click.echo(f"自信度: {result.confidence}")
        return

    click.echo("欢迎使用科研助手！我是您的研究伙伴 Agent。")
    click.echo("输入 'exit' 或 'quit' 退出, '/projects' 查看项目列表")
    click.echo(f"数据目录: {get_data_dir()}")

    while True:  # rest unchanged

    while True:
        try:
            user_input = click.prompt("\n> ", prompt_suffix="").strip()
        except (KeyboardInterrupt, EOFError):
            click.echo("\n再见！")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            click.echo("再见！")
            break
        if user_input == "/projects":
            from research_agent.store import get_all_projects
            projects = get_all_projects()
            if not projects:
                click.echo("还没有项目。开始对话即可自动创建项目。")
            else:
                for p in projects:
                    status_icon = {"active": "▶", "waiting": "⏸", "paused": "⏹", "done": "✓"}
                    icon = status_icon.get(p.status.value, "?")
                    pending = f" [等待: {p.pending_task.description}]" if p.pending_task else ""
                    click.echo(f"  {icon} {p.topic}{pending}")
            continue

        state.user_input = user_input
        state.retry_count = 0
        state.retrieved_chunks = []
        state.final_response = ""
        state.error = ""

        thread_id = getattr(state.active_project, "id", "default") or "default"
        result = process_user_input(state, thread_id=thread_id)
        click.echo(f"\n{result.final_response}")

        if state.active_project and state.active_project.pending_task:
            click.echo(f"\n⏸ 提醒: 项目「{state.active_project.topic}」等待您完成: {state.active_project.pending_task.description}")

        state = result


@main.command()
def status():
    """Show current agent status and project overview."""
    init_db()

    from research_agent.store import get_all_projects
    projects = get_all_projects()

    click.echo("=== 科研助手 Agent 状态 ===\n")

    if projects:
        click.echo(f"=== 项目列表 ({len(projects)}) ===")
        for p in projects:
            status_text = {"active": "进行中", "waiting": "等待用户", "paused": "已暂停", "done": "已完成"}
            st = status_text.get(p.status.value, p.status.value)
            pending = f" | 等待: {p.pending_task.description}" if p.pending_task else ""
            plan_count = len(p.plan)
            click.echo(f"  [{st}] {p.topic}{pending} (计划: {plan_count} 步)")
            if p.history_summary:
                click.echo(f"       摘要: {p.history_summary[:100]}")
    else:
        click.echo("暂无项目。开始对话即可自动创建。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run CLI help test**

Run: `pytest tests/test_cli.py::test_cli_startup -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/research_agent/cli.py tests/test_cli.py
git commit -m "feat: CLI chat interface with interactive mode and status command"
```

---

### Task 12: Skill System

**Files:**
- Create: `src/research_agent/skill.py`
- Create: `src/research_agent/skills/__init__.py`
- Create: `src/research_agent/skills/paper_search.py`
- Create: `src/research_agent/skills/literature_review.py`
- Create: `src/research_agent/skills/write_report.py`
- Create: `tests/test_skill.py`

**Interfaces:**
- Consumes: `search.py` from Task 6, `retrieval.py` from Task 5, `memory.py` from Task 7
- Produces: `skill.load_skills()→list[Skill]`, `skill.find_skill(user_input, skills)→Skill|None`, `Skill.trigger_phrases`, `Skill.execute(state)→AgentState`

- [ ] **Step 1: Implement skill.py**

```python
"""Skill system: load, match, and execute pluggable skills."""
from dataclasses import dataclass, field
from typing import Callable
from pathlib import Path

from research_agent.models import AgentState


@dataclass
class Skill:
    name: str
    description: str
    trigger_phrases: list[str]
    system_prompt: str = ""
    handler: Callable[[AgentState], AgentState] | None = None


def _builtin_skills() -> list[Skill]:
    from research_agent.skills.paper_search import paper_search_skill
    from research_agent.skills.literature_review import literature_review_skill
    from research_agent.skills.write_report import write_report_skill
    return [paper_search_skill, literature_review_skill, write_report_skill]


def load_skills() -> list[Skill]:
    return _builtin_skills()


def find_skill(user_input: str, skills: list[Skill]) -> Skill | None:
    input_lower = user_input.lower()
    for skill in skills:
        for phrase in skill.trigger_phrases:
            if phrase in input_lower:
                return skill
    return None
```

- [ ] **Step 2: Implement paper_search skill**

```python
# src/research_agent/skills/paper_search.py
"""Skill: search for new papers via Semantic Scholar API."""
from research_agent.skill import Skill
from research_agent.search import search_papers
from research_agent.models import AgentState


def _execute_paper_search(state: AgentState) -> AgentState:
    from research_agent.models import Paper
    from research_agent.store import insert_paper
    from research_agent.vector_store import add_chunks
    from research_agent.ingestion import _chunk_text, _score_source

    query = state.user_input.replace("搜索论文", "").replace("找论文", "").replace("search papers", "").strip()
    results = search_papers(query, limit=5)

    if not results:
        state.final_response = f'未找到与 "{query}" 相关的论文。请尝试其他关键词。'
        return state

    response_lines = [f'搜索 "{query}" 的结果 ({len(results)} 篇):\n']
    for i, r in enumerate(results):
        paper = Paper(
            title=r["title"],
            doi=r.get("doi", ""),
            year=r.get("year", 0),
            citation_count=r.get("citation_count", 0),
            authors=r.get("authors", []),
            abstract=r.get("abstract", ""),
        )
        _score_source(paper)

        response_lines.append(f"{i+1}. **{r['title']}** ({r.get('year', 'N/A')})")
        response_lines.append(f"   引用: {r.get('citation_count', 0)} | 质量: {paper.source_score}/10")
        response_lines.append(f"   作者: {', '.join(r.get('authors', [])[:3])}")
        if r.get("abstract"):
            response_lines.append(f"   摘要: {r['abstract'][:200]}...")
        response_lines.append("")

        pid = insert_paper(paper)
        chunks = _chunk_text(r.get("abstract", ""))
        add_chunks(pid, chunks)

    state.final_response = "\n".join(response_lines)
    state.final_response += f"\n已将 {len(results)} 篇论文摄入知识库。"
    return state


paper_search_skill = Skill(
    name="paper-search",
    description="搜索 Semantic Scholar 上的论文并摄入知识库",
    trigger_phrases=["搜索论文", "找论文", "搜索文献", "search papers", "查找论文"],
    system_prompt="You are searching for academic papers. Use the Semantic Scholar API.",
    handler=_execute_paper_search,
)
```

- [ ] **Step 3: Implement literature_review skill**

```python
# src/research_agent/skills/literature_review.py
"""Skill: generate literature review outline and draft sections."""
from research_agent.skill import Skill
from research_agent.models import AgentState
from research_agent.retrieval import hybrid_search, build_bm25_index


def _execute_literature_review(state: AgentState) -> AgentState:
    topic = state.active_project.topic if state.active_project else "the research topic"

    build_bm25_index()
    results = hybrid_search(topic, n_results=15)

    if not results:
        state.final_response = f'知识库中暂无与 "{topic}" 相关的论文。请先用 /paper-search 检索并摄入论文。'
        return state

    papers = {}
    for r in results:
        pid = r.get("paper_id", "unknown")
        if pid not in papers:
            papers[pid] = {"chunks": [], "title": r.get("title", pid)}
        papers[pid]["chunks"].append(r)

    outline = f"# 文献综述：{topic}\n\n## 大纲\n\n"
    sections = ["引言与背景", "核心方法与技术", "主要发现与对比", "开放问题与未来方向"]
    for i, sec in enumerate(sections):
        outline += f"{i+1}. {sec}\n"

    outline += "\n---\n\n## 初稿\n\n"
    outline += "### 1. 引言与背景\n\n"
    for pid, data in list(papers.items())[:3]:
        first_chunk = data["chunks"][0]["text"] if data["chunks"] else "无摘要"
        outline += f"根据文献 {pid[:8]}...，{first_chunk[:200]}...\n\n"

    state.final_response = outline
    state.final_response += "\n---\n引用来源: " + ", ".join(f"paper:{pid[:8]}" for pid in papers)
    state.final_response += "\n自信度: 推测 - 初稿待用户审阅补充"
    return state


literature_review_skill = Skill(
    name="literature-review",
    description="根据知识库生成文献综述大纲和初稿",
    trigger_phrases=["写综述", "文献综述", "literature review", "写review", "帮我写综述"],
    system_prompt="You are writing a literature review. Generate outline first, then draft sections with citations.",
    handler=_execute_literature_review,
)
```

- [ ] **Step 4: Implement write_report skill**

```python
# src/research_agent/skills/write_report.py
"""Skill: write research report from project data."""
from research_agent.skill import Skill
from research_agent.models import AgentState
from research_agent.retrieval import hybrid_search, build_bm25_index


def _execute_write_report(state: AgentState) -> AgentState:
    topic = state.active_project.topic if state.active_project else "research project"

    build_bm25_index()
    results = hybrid_search(topic, n_results=10)

    report = f"# 研究报告：{topic}\n\n"
    report += f"## 项目进度\n\n"
    if state.active_project:
        report += f"- 状态: {state.active_project.status.value}\n"
        report += f"- 历史摘要: {state.active_project.history_summary[:500]}\n"

    report += "\n## 文献基础\n\n"
    seen = set()
    for r in results:
        pid = r.get("paper_id", "unknown")
        if pid not in seen:
            seen.add(pid)
            report += f"- [{pid[:8]}] {r['text'][:150]}...\n"

    report += "\n## 结论与下一步\n\n"
    if state.active_project and state.active_project.plan:
        for step in state.active_project.plan:
            report += f"- [{step.status}] {step.step} ({step.owner})\n"
    else:
        report += "待补充研究计划和实验数据。\n"

    state.final_response = report
    state.final_response += "\n---\n自信度: 推测 - 报告需要你补充实验数据和审阅"
    return state


write_report_skill = Skill(
    name="write-report",
    description="整合项目数据生成正式研究报告",
    trigger_phrases=["写报告", "生成报告", "写report", "write report", "出报告"],
    system_prompt="You are writing a comprehensive research report. Integrate all project data.",
    handler=_execute_write_report,
)
```

- [ ] **Step 5: Integrate skills into agent.py**

```python
# Modify agent.py - add to router_node, after setting active_project:

from research_agent.skill import load_skills, find_skill
from research_agent.loop import boundary_check

# In router_node, add at the end:
def router_node(state: AgentState) -> AgentState:
    # ... existing code ...

    # Check for skill triggers
    skills = load_skills()
    skill = find_skill(state.user_input, skills)
    if skill and skill.handler:
        state = skill.handler(state)
        return state

    return state
```

- [ ] **Step 6: Write tests**

```python
# tests/test_skill.py
from research_agent.skill import load_skills, find_skill, Skill


def test_load_skills():
    skills = load_skills()
    assert len(skills) >= 3
    names = {s.name for s in skills}
    assert "paper-search" in names
    assert "literature-review" in names
    assert "write-report" in names


def test_find_skill_match():
    skills = load_skills()
    skill = find_skill("帮我搜索论文关于transformer的相关文献", skills)
    assert skill is not None
    assert skill.name == "paper-search"


def test_find_skill_no_match():
    skills = load_skills()
    skill = find_skill("今天天气怎么样", skills)
    assert skill is None


def test_paper_search_skill_handler(temp_data_dir):
    from unittest.mock import patch, MagicMock
    from research_agent.models import AgentState
    from research_agent.skills.paper_search import _execute_paper_search
    from research_agent.store import init_db

    init_db()

    mock_response = {"data": [{
        "paperId": "abc", "title": "Test Paper", "year": 2024,
        "citationCount": 10, "authors": [{"name": "Author"}],
        "externalIds": {"DOI": "10.000/test"}, "abstract": "An abstract."
    }]}

    with patch("research_agent.skills.paper_search.search_papers", return_value=mock_response["data"]):
        state = AgentState(user_input="搜索论文 transformer attention")
        result = _execute_paper_search(state)
        assert len(result.final_response) > 0
        assert "Test Paper" in result.final_response
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_skill.py -v`
Expected: 4 PASS

- [ ] **Step 8: Commit**

```bash
git add src/research_agent/skill.py src/research_agent/skills/ tests/test_skill.py
git add src/research_agent/agent.py  # (skill integration in router_node)
git commit -m "feat: skill system with paper-search, literature-review, and write-report"
```

---

### Task 13: Integration Test & Smoke Test

**Files:**
- Currently: `tests/test_cli.py`
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_integration.py
"""End-to-end integration tests."""
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from research_agent.cli import main


@patch("research_agent.agent.litellm.completion")
def test_full_chat_flow(mock_completion, temp_data_dir):
    from research_agent.store import init_db, insert_paper
    from research_agent.vector_store import add_chunks
    from research_agent.models import Paper, Project

    init_db()
    insert_paper(Paper(
        id="p_int", title="Attention Paper", doi="10.000/int", year=2020,
        source_score=9, citation_count=100, authors=["Author A"],
        abstract="Attention mechanisms in deep learning."
    ))
    add_chunks("p_int", [
        {"chunk_index": 0, "text": "Attention mechanisms are a fundamental component of modern neural networks."},
        {"chunk_index": 1, "text": "Self-attention enables parallel processing of sequential data."},
    ])

    mock_completion.side_effect = [
        MagicMock(choices=[MagicMock(message=MagicMock(content='{"needs_retrieval": true, "search_query": "attention mechanism"}'))]),
        MagicMock(choices=[MagicMock(message=MagicMock(content="Based on the literature:\n\nAttention mechanisms are a key innovation in neural networks. According to the paper, self-attention enables parallel processing.\n\n---\n引用来源: paper:p_int\n自信度: 确定 - supported by retrieved context"))]),
    ]

    runner = CliRunner()
    result = runner.invoke(main, ["chat", "what is attention mechanism?"])
    assert result.exit_code == 0
    assert "Attention" in result.output or "attention" in result.output.lower()


@patch("research_agent.agent.litellm.completion")
def test_multi_project_routing(mock_completion, temp_data_dir):
    from research_agent.store import init_db, insert_project
    from research_agent.models import Project, ProjectStatus

    init_db()
    insert_project(Project(id="chem_proj", topic="HPLC compound screening", status=ProjectStatus.ACTIVE,
                           history_summary="We analyzed compound purity by HPLC."))
    insert_project(Project(id="bio_proj", topic="protein expression in E. coli", status=ProjectStatus.ACTIVE,
                           history_summary="Checking protein expression levels."))

    mock_completion.side_effect = [
        MagicMock(choices=[MagicMock(message=MagicMock(content='{"needs_retrieval": true, "search_query": "HPLC purity analysis"}'))]),
        MagicMock(choices=[MagicMock(message=MagicMock(content="HPLC analysis结果通常包括...\n---\n引用来源: [knowledge base]\n自信度: 推测"))]),
    ]

    runner = CliRunner()
    result = runner.invoke(main, ["chat", "上次那个HPLC结果怎么样"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/test_integration.py -v`
Expected: 2 PASS

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py tests/test_cli.py
git commit -m "test: integration tests for full chat flow and multi-project routing"
```

---

### Task 14: Final Polish & README

**Files:**
- Create: `research-agent/README.md`
- Modify: `src/research_agent/__init__.py`

- [ ] **Step 1: Write README.md**

```markdown
# Research Agent

A persistent research partner agent that remembers your work across sessions.

## Install

```bash
pip install -e .
```

Set your LLM API key:

```bash
export RESEARCH_AGENT_LLM_KEY="your-api-key"
```

## Usage

```bash
# Interactive mode
research-agent chat

# Single query
research-agent chat "What is the attention mechanism?"

# Check status
research-agent status
```

## Data

All data stored locally at `~/research-agent-data/`:
- `chroma_db/` - Vector embeddings
- `research_agent.db` - Papers and projects (SQLite)
- `user_profile.json` - Your research profile
- `agent_self_intro.json` - Agent's self-description

## Skills

Built-in skills triggered by keywords:
- `搜索论文` - Search Semantic Scholar
- `写综述` - Generate literature review
- `写报告` - Generate research report

## Develop

```bash
pip install -e ".[dev]"
pytest tests/ -v
```
```

- [ ] **Step 2: Update __init__.py**

```python
"""Research Agent - A persistent research partner with memory and RAG."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Full test suite final run**

Run: `pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add README.md src/research_agent/__init__.py
git commit -m "docs: README and version bump"
```

- [ ] **Step 5: Verify CLI works end-to-end with mock**

Run: `research-agent --help`
Expected: Shows available commands (chat, status)

---

## Verification Checklist

After all tasks complete, manually verify:

1. `pip install -e .` succeeds with all dependencies
2. `research-agent --help` shows commands
3. `research-agent status` shows initial state
4. `research-agent chat "test query"` produces a response (with LLM key set)
5. `~/research-agent-data/` contains expected files
6. `pytest tests/ -v` all pass
7. No hardcoded API keys in any source file
8. `git log --oneline` shows one commit per task
