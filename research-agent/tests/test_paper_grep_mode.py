# tests/test_paper_grep_mode.py — V4 two-phase paper landing (staging → formal)
import os
import shutil

import pytest

from research_agent import paper_store
from research_agent.models import AgentState
from research_agent.tools.builtin.retrieve import (
    _handle_read_paper, _handle_retrieve, _handle_search,
)

PAPER_ID = "2501.00001"


def _mk_state(workspace) -> AgentState:
    s = AgentState(user_input="test")
    s.workspace_dir = workspace
    from research_agent.models import Project, ProjectStatus
    s.active_project = Project(id="proj1", topic="test", status=ProjectStatus.ACTIVE)
    return s


def _mock_emit(et: str, d: dict):
    pass


@pytest.fixture
def workspace():
    d = os.path.realpath(os.path.join(os.getcwd(), "tmp_ws_test"))
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ── paper_store zone semantics ──────────────────────────────────────────────

def test_paper_store_zones_separate(workspace):
    staging = paper_store.staging_path(workspace, PAPER_ID)
    formal = paper_store.formal_path(workspace, PAPER_ID)
    assert ".research-agent" in staging.replace("\\", "/")
    assert not formal.endswith(".research-agent")
    # writing staging does not create formal
    paper_store.write_md(staging, {"title": "T"}, "body")
    assert os.path.isfile(staging)
    assert not os.path.isfile(formal)


def test_paper_store_promote_moves_file(workspace):
    paper_store.write_md(paper_store.staging_path(workspace, PAPER_ID),
                         {"title": "T", "authors": ["a"]}, "body text")
    dst = paper_store.promote_to_formal(workspace, PAPER_ID)
    assert dst and os.path.isfile(dst)
    assert not os.path.isfile(paper_store.staging_path(workspace, PAPER_ID))
    entry = paper_store.read_md(dst)
    assert entry["text"] == "body text"
    assert entry["meta"]["title"] == "T"


def test_paper_store_promote_noop_when_missing(workspace):
    assert paper_store.promote_to_formal(workspace, "nope") is None


# ── read_paper: persist=false lands in staging only ─────────────────────────

def test_read_paper_persist_false_lands_in_staging(workspace, monkeypatch):
    monkeypatch.setattr(
        "research_agent.search.get_paper_metadata",
        lambda pid, id_type="arxiv": {
            "title": "A Paper", "authors": ["X"], "year": 2025,
            "abstract": "An abstract.", "doi": f"arxiv:{pid}", "source": "arxiv",
        },
    )
    monkeypatch.setattr(
        "research_agent.tools.arxiv_pdf.fetch_pdf_text",
        lambda pid, timeout=90: ("full body text about transformers. " * 20, ""),
    )
    state = _mk_state(workspace)
    result = _handle_read_paper({"paper_id": PAPER_ID}, None, state, _mock_emit)
    assert result.success
    assert "full body text" in result.data["full_text"]
    # staged, not formal
    assert os.path.isfile(paper_store.staging_path(workspace, PAPER_ID))
    assert not os.path.isfile(paper_store.formal_path(workspace, PAPER_ID))
    assert result.data["persisted"] is False
    # not grep-able until persisted
    hits = _handle_retrieve({"query": "transformers"}, None, state, _mock_emit)
    assert hits.success is False or hits.data.get("found", 0) == 0


# ── read_paper: persist=true promotes staging → formal ─────────────────────

def test_read_paper_persist_true_formal(workspace, monkeypatch):
    monkeypatch.setattr(
        "research_agent.search.get_paper_metadata",
        lambda pid, id_type="arxiv": {
            "title": "A Paper", "authors": ["X"], "year": 2025,
            "abstract": "An abstract.", "doi": f"arxiv:{pid}", "source": "arxiv",
        },
    )
    monkeypatch.setattr(
        "research_agent.tools.arxiv_pdf.fetch_pdf_text",
        lambda pid, timeout=90: ("full body about RLHF alignment. " * 20, ""),
    )
    state = _mk_state(workspace)
    result = _handle_read_paper({"paper_id": PAPER_ID, "persist": True}, None, state, _mock_emit)
    assert result.success
    assert os.path.isfile(paper_store.formal_path(workspace, PAPER_ID))
    assert not os.path.isfile(paper_store.staging_path(workspace, PAPER_ID))
    assert result.data["persisted"] is True
    # now grep-able
    hits = _handle_retrieve({"query": "RLHF"}, None, state, _mock_emit)
    assert hits.success
    assert any(r["paper_id"] == PAPER_ID for r in hits.data.get("items", []))


# ── read_paper two-step: read → confirm → persist ──────────────────────────

def test_read_paper_then_persist_two_phase(workspace, monkeypatch):
    calls = {"n": 0}

    def fake_fetch(pid, timeout=90):
        calls["n"] += 1
        return ("body about graph neural networks. " * 20, "")

    monkeypatch.setattr(
        "research_agent.search.get_paper_metadata",
        lambda pid, id_type="arxiv": {
            "title": "GNN", "authors": ["Y"], "year": 2025,
            "abstract": "GNN abstract.", "doi": f"arxiv:{pid}", "source": "arxiv",
        },
    )
    monkeypatch.setattr("research_agent.tools.arxiv_pdf.fetch_pdf_text", fake_fetch)

    state = _mk_state(workspace)
    # phase 1: temp read
    r1 = _handle_read_paper({"paper_id": PAPER_ID}, None, state, _mock_emit)
    assert r1.success and r1.data["persisted"] is False
    # phase 2: confirm → persist (reuses staged file, no second download)
    r2 = _handle_read_paper({"paper_id": PAPER_ID, "persist": True}, None, state, _mock_emit)
    assert r2.success and r2.data["persisted"] is True
    assert os.path.isfile(paper_store.formal_path(workspace, PAPER_ID))
    # no redundant re-download for the second call
    assert calls["n"] == 1


# ── read_paper rejects unknown local paper when arXiv fetch returns nothing ─

def test_read_paper_unknown_id_fails(workspace, monkeypatch):
    monkeypatch.setattr(
        "research_agent.search.get_paper_metadata",
        lambda pid, id_type="arxiv": None,
    )
    state = _mk_state(workspace)
    result = _handle_read_paper({"paper_id": "9999.99999"}, None, state, _mock_emit)
    assert not result.success


# ── delete_paper removes both zones ─────────────────────────────────────────

def test_delete_paper_removes_zones(workspace, monkeypatch):
    from research_agent.tools.builtin.retrieve import _handle_delete_paper
    paper_store.write_md(paper_store.staging_path(workspace, PAPER_ID), {}, "tmp")
    paper_store.write_md(paper_store.formal_path(workspace, PAPER_ID), {}, "formal")
    state = _mk_state(workspace)
    result = _handle_delete_paper({"paper_id": PAPER_ID}, None, state, _mock_emit)
    assert result.success
    assert not os.path.exists(paper_store.formal_path(workspace, PAPER_ID))
    assert not os.path.exists(paper_store.staging_path(workspace, PAPER_ID))


# ── retrieve empty hint directs to search / persist ─────────────────────────

def test_retrieve_empty_hints(workspace):
    state = _mk_state(workspace)
    result = _handle_retrieve({"query": "nothing matches this"}, None, state, _mock_emit)
    assert not result.success
    hint = result.data.get("hint", "")
    assert "search_papers" in hint and "persist=true" in hint


# ── search_papers does not write any file ──────────────────────────────────

def test_search_papers_never_lands_files(workspace, monkeypatch):
    monkeypatch.setattr(
        "research_agent.search.search_papers",
        lambda query, limit=5: [{
            "title": "Search Hit", "year": 2025, "arxiv_id": PAPER_ID,
            "authors": ["Z"], "abstract": "abstract for search test",
        }],
    )
    state = _mk_state(workspace)
    result = _handle_search({"query": "test"}, None, state, _mock_emit)
    assert result.success
    assert not os.path.exists(paper_store.staging_path(workspace, PAPER_ID))
    assert not os.path.exists(paper_store.formal_path(workspace, PAPER_ID))
