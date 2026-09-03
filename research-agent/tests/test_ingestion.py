# tests/test_ingestion.py — deterministic text-processing helpers (no vector store)
#
# Vector chunk embedding / source-merge helpers were retired in V4 (grep mode);
# the remaining helpers are pure text logic and stay unit-tested here.

from research_agent.ingestion import (
    _clean_text, _chunk_text_with_sections, _should_accept,
    deduplicate_by_title,
)
from research_agent.models import Paper


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


def test_deduplicate_by_title_exact(temp_data_dir):
    p = Paper(id="dup_1", title="Attention Is All You Need", doi="10.1/x",
              year=2017, source_score=9, citation_count=10)
    from research_agent.store import insert_paper, init_db
    init_db()
    insert_paper(p)
    hit = deduplicate_by_title("Attention Is All You Need")
    assert hit is not None
    assert hit.id == "dup_1"


def test_deduplicate_by_title_fuzzy(temp_data_dir):
    p = Paper(id="dup_2", title="A Study of Neural Attention Mechanisms", doi="10.1/y",
              year=2020, source_score=9, citation_count=10)
    from research_agent.store import insert_paper, init_db
    init_db()
    insert_paper(p)
    hit = deduplicate_by_title("A study of neural attention mechanism")
    assert hit is not None
    assert hit.id == "dup_2"
