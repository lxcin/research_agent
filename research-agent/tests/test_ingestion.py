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
    import json
    import litellm
    from research_agent.vector_store import get_collection

    pid_existing = "paper_existing"
    pid_new = "paper_new"
    add_chunks(pid_existing, [
        {"chunk_index": 0, "text": "The temperature increase of 10C shifts retention time by 0.3 min in HPLC analysis."},
    ])
    add_chunks(pid_new, [
        {"chunk_index": 0, "text": "In HPLC, a 10C temperature rise causes retention time to shift forward by approximately 0.3 min."},
    ])

    with patch("research_agent.ingestion.litellm.completion") as mock_llm:
        mock_llm.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"relation": "agree", "explanation": "Both state same quantitative relationship"}'))]
        )
        _detect_and_merge_sources(pid_new, [
            {"chunk_index": 0, "text": "In HPLC, a 10C temperature rise causes retention time to shift forward by approximately 0.3 min."}
        ])

    coll = get_collection()
    result = coll.get(ids=[f"{pid_existing}_chunk_0"])
    assert result["metadatas"]
    sources_raw = result["metadatas"][0].get("verified_sources", "[]")
    sources = json.loads(sources_raw) if isinstance(sources_raw, str) else sources_raw
    assert any(s["paper_id"] == pid_new for s in sources)