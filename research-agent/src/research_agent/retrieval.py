"""Keyword/grep retrieval over formal paper .md files (Tier A working memory).

Replaces the retired Chroma/BM25/RRF hybrid paper retrieval. Search is scoped
to a papers directory (workspace/papers/*.md) and ranks files by query-term
coverage using jieba tokenization for mixed Chinese/English queries.
"""
import os
import re

import jieba


def _tokenize(text: str) -> list[str]:
    if any('\u4e00' <= c <= '\u9fff' for c in text):
        tokens = list(jieba.cut(text))
        tokens += re.findall(r'[a-zA-Z0-9]+', text.lower())
    else:
        tokens = re.findall(r'[a-zA-Z0-9]+', text.lower())
    return [t for t in tokens if t.strip()]


def _paper_id_from_filename(fname: str) -> str:
    return os.path.splitext(os.path.basename(fname))[0]


def _title_from_md(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _snippet_around_hits(text: str, terms: set[str], max_chars: int = 500) -> str:
    """Return a window of text around the first line containing any query term."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if any(t.lower() in line.lower() for t in terms):
            start = max(0, i - 1)
            end = min(len(lines), i + 8)
            return "\n".join(lines[start:end])[:max_chars]
    return lines[0][:max_chars] if lines else ""


def grep_papers(query: str, papers_dir: str, n_results: int = 5) -> list[dict]:
    """Keyword search over *.md files in papers_dir. Returns scored hits.

    Each hit: {paper_id, title, text(snippet), score, source="local"}.
    Empty dir or unreadable files degrade to [] (never raises).
    """
    if not query.strip() or not os.path.isdir(papers_dir):
        return []
    q_terms = _tokenize(query)
    if not q_terms:
        return []
    q_set = set(q_terms)

    scored: list[tuple[float, dict]] = []
    try:
        files = sorted(f for f in os.listdir(papers_dir) if f.endswith(".md"))
    except OSError:
        return []

    for fname in files:
        path = os.path.join(papers_dir, fname)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        if not text.strip():
            continue
        doc_terms = _tokenize(text)
        # Coverage score: share of distinct query terms present in doc.
        present = set(doc_terms) & q_set
        if not present:
            continue
        coverage = len(present) / len(q_set)
        # Frequency bonus on present terms.
        freq = sum(1 for t in doc_terms if t in present)
        score = 0.5 * coverage + 0.5 * min(freq / max(len(q_set) * 5, 1), 1.0)

        pid = _paper_id_from_filename(fname)
        scored.append((score, {
            "paper_id": pid,
            "title": _title_from_md(text) or pid,
            "text": _snippet_around_hits(text, present),
            "score": score,
            "source": "local",
        }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:n_results]]


# ── Compatibility shims (retired vector/BMRR chain) ──────────────────────────
# Kept so historical import sites (agent.py / server.py) do not break. They no
# longer touch Chroma/BM25: vector paper retrieval is retired in V4 (grep mode).
def is_vector_available() -> bool:
    """Vector paper retrieval is retired in V4 — always False."""
    return False


def hybrid_search(query: str, n_results: int = 5, project_id: str | None = None,
                  papers_dir: str | None = None) -> list[dict]:
    """Deprecated. Forwarded to grep_papers when a papers_dir is provided."""
    if papers_dir:
        return grep_papers(query, papers_dir, n_results=n_results)
    return []


def build_bm25_index():
    """Deprecated no-op (vector BM25 index retired in V4)."""
    return None


# Backward-compatible aliases (same tokenizer). Old vector tests imported these
# names; semantic split between query/doc no longer applies.
_tokenize_query = _tokenize
_tokenize_doc = _tokenize
