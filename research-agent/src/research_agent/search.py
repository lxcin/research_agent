"""Semantic Scholar API client for paper search and metadata."""
import httpx
import time

S2_BASE = "https://api.semanticscholar.org/graph/v1"
S2_SEARCH = f"{S2_BASE}/paper/search"
S2_PAPER = f"{S2_BASE}/paper"
S2_FIELDS = "title,year,citationCount,authors,externalIds,abstract,venue,journal"


def _extract_venue(venue):
    if venue is None:
        return ""
    if isinstance(venue, dict):
        return venue.get("name", "")
    if isinstance(venue, str):
        return venue
    return str(venue)


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
"venue": _extract_venue(paper.get("venue")),
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
        "venue": _extract_venue(paper.get("venue")),
    }