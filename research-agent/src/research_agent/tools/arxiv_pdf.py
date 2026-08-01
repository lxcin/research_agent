"""arXiv PDF download and ingestion — full paper text pipeline."""
import logging
import tempfile
import httpx
import os
import re
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


def _arxiv_pdf_url(arxiv_id: str) -> str:
    """Get PDF URL from arXiv ID. Strips version suffix (v1, v2, etc.)."""
    base = re.sub(r"v\d+$", "", arxiv_id)
    return f"https://arxiv.org/pdf/{base}.pdf"


def download_arxiv_pdf(arxiv_id: str, timeout: int = 60) -> Path | None:
    """Download arXiv PDF to a temp file. Returns path or None."""
    url = _arxiv_pdf_url(arxiv_id)
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        if resp.status_code != 200:
            return None
        # Check if it's actually a PDF (some arXiv IDs redirect to abstract page)
        content_type = resp.headers.get("content-type", "")
        if "pdf" not in content_type and len(resp.content) < 10000:
            return None
        fd, path = tempfile.mkstemp(suffix=".pdf", prefix=f"arxiv_{arxiv_id}_")
        os.write(fd, resp.content)
        os.close(fd)
        return Path(path)
    except Exception as e:
        logger.warning(f"Failed to download arXiv PDF {arxiv_id}: {e}")
        return None


def ingest_arxiv_pdf(arxiv_id: str, meta: dict | None = None) -> tuple[str | None, str]:
    """Download and ingest an arXiv paper's PDF. Returns (full_text, error).
    Uses existing ingest_pdf pipeline from ingestion.py."""
    from research_agent.ingestion import ingest_pdf
    pdf_path = download_arxiv_pdf(arxiv_id)
    if not pdf_path:
        return None, "PDF download failed or not available"

    try:
        paper, msg = ingest_pdf(str(pdf_path))
        if paper:
            # Update paper metadata from arXiv if available
            if meta:
                from research_agent.store import insert_paper, get_paper
                existing = get_paper(paper.id)
                if existing:
                    existing.title = meta.get("title", existing.title)
                    existing.authors = meta.get("authors", existing.authors)
                    existing.year = meta.get("year", existing.year)
                    existing.doi = f"arxiv:{arxiv_id}"
                    insert_paper(existing)
            # Return full text from ChromaDB
            from research_agent.ingestion import recall_full_paper
            full_text = recall_full_paper(paper.id)
            return full_text or "", ""
        return None, msg
    except Exception as e:
        return None, str(e)
    finally:
        try:
            os.unlink(pdf_path)
        except Exception:
            pass


def ingest_arxiv_async(arxiv_id: str, meta: dict | None = None,
                       on_done=None, on_error=None):
    """Download and ingest arXiv PDF in background thread. Calls on_done(text) or on_error(msg)."""
    def _worker():
        try:
            text, error = ingest_arxiv_pdf(arxiv_id, meta)
            if text and on_done:
                on_done(text)
            elif on_error:
                on_error(error or "Unknown error")
        except Exception as e:
            if on_error:
                on_error(str(e))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def delete_paper_from_kb(paper_id: str) -> bool:
    """Remove a paper from ChromaDB + SQLite + workspace. Returns success."""
    try:
        from research_agent.vector_store import delete_paper as vec_delete
        vec_delete(paper_id)
    except Exception:
        pass
    try:
        from research_agent.store import delete_paper as db_delete
        db_delete(paper_id)
    except Exception:
        return False
    return True
