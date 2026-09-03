"""arXiv PDF download and text extraction — Tier A grep mode (no vector store).

Downloads an arXiv PDF, extracts plain text with pymupdf, and (via the caller)
lands it into a paper .md staging/formal file. No Chroma/ingestion involvement.
"""
import logging
import tempfile
import httpx
import os
import re
import pymupdf
from pathlib import Path

from research_agent import paper_store

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


def extract_pdf_text(pdf_path: str, max_pages: int = 50) -> str:
    """Extract plain text from a PDF file with pymupdf."""
    doc = pymupdf.open(pdf_path)
    text = ""
    for i in range(min(len(doc), max_pages)):
        text += doc.load_page(i).get_text() + "\n"
    doc.close()
    return text.strip()


def fetch_pdf_text(arxiv_id: str, timeout: int = 90) -> tuple[str | None, str]:
    """Download + extract an arXiv paper's full text.
    Returns (full_text, error). full_text is None when download/parse fails."""
    pdf_path = download_arxiv_pdf(arxiv_id, timeout=timeout)
    if not pdf_path:
        return None, "PDF download failed or not available"
    try:
        text = extract_pdf_text(str(pdf_path))
        return (text, "") if text else (None, "PDF parsed to empty text")
    except Exception as e:
        return None, f"PDF parse failed: {e}"
    finally:
        try:
            os.unlink(pdf_path)
        except Exception:
            pass


def delete_paper_files(workspace_dir: str, paper_id: str) -> bool:
    """Remove a paper from formal + staging md zones. Returns True if any removed."""
    removed = False
    formal = paper_store.formal_path(workspace_dir, paper_id)
    if os.path.isfile(formal):
        try:
            os.unlink(formal)
            removed = True
        except OSError:
            pass
    if paper_store.discard_staging(workspace_dir, paper_id):
        removed = True
    return removed
