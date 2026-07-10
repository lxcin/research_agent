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