"""Pytest fixtures for research-agent tests."""
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def temp_data_dir(monkeypatch):
    """Redirect data dir to temp for tests. Also reset global DB/collection state."""
    import research_agent.store as store_mod
    import research_agent.vector_store as vs_mod
    store_mod._DB = None
    vs_mod._COLLECTIONS.clear()

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