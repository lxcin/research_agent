"""arXiv API client for paper search and metadata."""
import httpx
import xml.etree.ElementTree as ET
import time
from datetime import datetime

ARXIV_API = "http://export.arxiv.org/api/query"
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _parse_arxiv_entry(entry) -> dict:
    """Parse a single arXiv Atom entry into a paper dict."""
    title = entry.find("atom:title", ARXIV_NS)
    title = title.text.strip().replace("\n", " ") if title is not None and title.text else ""

    abstract = entry.find("atom:summary", ARXIV_NS)
    abstract = abstract.text.strip().replace("\n", " ") if abstract is not None and abstract.text else ""

    arxiv_id = ""
    for id_elem in entry.findall("atom:id", ARXIV_NS):
        if id_elem.text:
            arxiv_id = id_elem.text.split("/abs/")[-1]
            break

    authors = []
    for author in entry.findall("atom:author", ARXIV_NS):
        name = author.find("atom:name", ARXIV_NS)
        if name is not None and name.text:
            authors.append(name.text.strip())

    published = entry.find("atom:published", ARXIV_NS)
    year = 0
    if published is not None and published.text:
        try:
            year = datetime.fromisoformat(published.text.replace("Z", "+00:00")).year
        except Exception:
            pass

    categories = []
    for cat in entry.findall("atom:category", ARXIV_NS):
        term = cat.get("term", "")
        if term:
            categories.append(term)

    return {
        "paper_id": arxiv_id,
        "title": title,
        "year": year,
        "citation_count": 0,
        "authors": authors,
        "doi": "",
        "abstract": abstract,
        "venue": ", ".join(categories[:3]) if categories else "",
        "source": "arxiv",
        "arxiv_id": arxiv_id,
    }


def search_papers(query: str, limit: int = 10, offset: int = 0) -> list[dict]:
    params = {
        "search_query": f"all:{query}",
        "start": offset,
        "max_results": min(limit, 10),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    for attempt in range(3):
        try:
            resp = httpx.get(ARXIV_API, params=params, timeout=30, follow_redirects=True)
            if resp.status_code == 503:
                time.sleep(3)
                continue
            if resp.status_code != 200:
                return []
            break
        except Exception:
            if attempt < 2:
                time.sleep(2)
            else:
                return []

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return []

    entries = root.findall("atom:entry", ARXIV_NS)
    return [_parse_arxiv_entry(e) for e in entries]


def get_paper_metadata(identifier: str, id_type: str = "arxiv") -> dict | None:
    if id_type == "arxiv":
        params = {"id_list": identifier}
    else:
        params = {"search_query": f"all:{identifier}", "max_results": 1}

    for attempt in range(3):
        try:
            resp = httpx.get(ARXIV_API, params=params, timeout=30, follow_redirects=True)
            if resp.status_code == 503:
                time.sleep(3)
                continue
            if resp.status_code != 200:
                return None
            break
        except Exception:
            if attempt < 2:
                time.sleep(2)
            else:
                return None

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return None

    entries = root.findall("atom:entry", ARXIV_NS)
    if not entries:
        return None
    return _parse_arxiv_entry(entries[0])


def _keep_search_papers():
    """Backward compatibility: Semantic Scholar search still available."""
    pass