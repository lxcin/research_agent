"""Traceable Multi-Source RAG ingestion pipeline.
Clean → Chunk → Filter → Embed → Store → Provenance."""

import json
import re
import uuid
from difflib import SequenceMatcher
from pathlib import Path

import litellm
import pymupdf

from research_agent.models import Paper
from research_agent.store import insert_paper, get_all_papers
from research_agent.vector_store import add_chunks, get_collection, search

# ---- Constants ----

REJECT_DOMAINS = ["zhihu.com", "medium.com", "blogspot.com", "mp.weixin.qq.com", "weixin.qq.com"]
MIN_ACCEPT_SCORE = 4

TOP_CONFERENCES = [
    "nature", "science", "cell", "pnas",
    "neurips", "icml", "iclr", "cvpr", "iccv", "eccv",
    "acl", "emnlp", "naacl",
    "aaai", "ijcai", "sigir", "www", "kdd",
    "jacs", "angewandte", "joc", "orglett",
]

# ---- Text Cleaning ----

def _clean_text(text: str) -> str:
    # Remove page numbers and running headers (isolated numeric lines)
    text = re.sub(r'^\d+\s*$', '', text, flags=re.MULTILINE)
    # Collapse whitespace but preserve paragraph breaks (double newlines)
    text = re.sub(r'[^\S\n]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _strip_references(text: str) -> str:
    """Remove reference section from text for chunking purposes."""
    ref_pattern = r'\n(#{1,4}\s*(References|Bibliography|参考文献|REFERENCES|BIBLIOGRAPHY))\n'
    match = re.search(ref_pattern, text, re.IGNORECASE)
    if match:
        return text[:match.start()]
    return text


# ---- Chunking ----

def _chunk_text_with_sections(text: str, min_tokens: int = 200,
                               max_tokens: int = 800,
                               overlap_sentences: int = 3) -> list[dict]:
    text = _strip_references(_clean_text(text))
    sections = re.split(r'(?=^#{1,4}\s)', text, flags=re.MULTILINE)
    chunks = []
    idx = 0

    for section in sections:
        header = section.split('\n')[0][:80].strip('#').strip() if section.startswith('#') else ''
        paragraphs = [p.strip() for p in section.split('\n\n') if p.strip()]
        current = []
        prev_chunk = None

        for para in paragraphs:
            words = para.split()
            wc = len(words)

            # Isolate special content
            if _is_table(para):
                chunks.append({
                    "text": para, "chunk_index": idx,
                    "section": header, "content_type": "table",
                })
                idx += 1; continue

            if _is_code_or_formula(para):
                chunks.append({
                    "text": para, "chunk_index": idx,
                    "section": header, "content_type": "formula",
                })
                idx += 1; continue

            if wc < 20:
                current.append(para)
                continue

            # Build current chunk
            current_wc = sum(len(p.split()) for p in current)
            if current_wc + wc > max_tokens and current:
                chunk_text = ' '.join(current)
                if prev_chunk:
                    overlap = _last_n_sentences(prev_chunk["text"], overlap_sentences)
                    chunk_text = overlap + '\n' + chunk_text
                chunks.append({
                    "text": chunk_text, "chunk_index": idx,
                    "section": header, "content_type": "paragraph",
                })
                idx += 1
                prev_chunk = chunks[-1] if chunks else None
                current = [para]
            else:
                current.append(para)

        if current:
            chunk_text = ' '.join(current)
            current_wc = len(chunk_text.split())
            if current_wc < min_tokens // 4 and chunks:
                chunks[-1]["text"] += '\n' + chunk_text
            else:
                if prev_chunk and idx > 0:
                    overlap = _last_n_sentences(prev_chunk["text"], overlap_sentences)
                    chunk_text = overlap + '\n' + chunk_text
                chunks.append({
                    "text": chunk_text, "chunk_index": idx,
                    "section": header, "content_type": "paragraph",
                })
                idx += 1
                prev_chunk = chunks[-1] if chunks else None

    return [c for c in chunks if c["text"].strip()]


def _is_table(text: str) -> bool:
    lines = text.strip().split('\n')
    return any('\t' in l or '|' in l for l in lines) and len(lines) >= 2


def _is_code_or_formula(text: str) -> bool:
    return text.strip().startswith('```') or text.strip().startswith('$$')


def _last_n_sentences(text: str, n: int) -> str:
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return ' '.join(sentences[-n:]) if len(sentences) >= n else text


# ---- Quality Scoring ----

def _score_source(paper: Paper) -> int:
    score = 3
    title_lower = paper.title.lower()
    for conf in TOP_CONFERENCES:
        if conf in title_lower:
            score += 4; break
    if paper.doi:
        if paper.citation_count > 100:
            score += 3
        elif paper.citation_count > 10:
            score += 2
        elif "arxiv" in paper.doi.lower() and paper.citation_count > 5:
            score += 1
    if paper.year < (2026 - 15) and paper.citation_count < 5:
        score = max(1, score - 1)
    if not paper.doi and "arxiv" not in (paper.doi or "").lower() and paper.citation_count == 0:
        score = 1
    for domain in REJECT_DOMAINS:
        if domain in paper.file_path.lower() or domain in paper.title.lower():
            score = 1; break
    paper.source_score = score
    return score


def _should_accept(paper: Paper) -> tuple[bool, str]:
    for domain in REJECT_DOMAINS:
        if domain in paper.file_path.lower() or domain in paper.title.lower():
            return False, f"非学术来源（{domain}），已拒绝"
    if not paper.doi and "arxiv" not in (paper.doi or "").lower() and paper.citation_count == 0:
        return False, "无DOI、无arXiv标识、无引用——无法验证学术来源"
    if paper.source_score < MIN_ACCEPT_SCORE:
        if paper.source_score == 2:
            return False, "来源评分过低，已隔离。可以通过手动操作入库。"
        return False, f"来源评分为 {paper.source_score}（最低要求 {MIN_ACCEPT_SCORE}），已拒绝"
    return True, ""


# ---- Core Ingestion ----

def _parse_pdf(file_path: str) -> str:
    doc = pymupdf.open(file_path)
    max_pages = min(len(doc), 50)
    text = ""
    for i in range(max_pages):
        text += doc.load_page(i).get_text() + "\n"
    doc.close()
    return text.strip()


def deduplicate_by_title(title: str) -> Paper | None:
    for paper in get_all_papers():
        if paper.title.lower().strip() == title.lower().strip():
            return paper
        if SequenceMatcher(None, paper.title.lower(), title.lower()).ratio() > 0.90:
            return paper
    return None


def _detect_and_merge_sources(paper_id: str, chunks: list[dict]):
    """Ingest-time provenance: agree → merge verified_sources; contradict → record conflict."""
    from research_agent.store import _get_db, init_conflict_table
    init_conflict_table()
    db = _get_db()
    coll = get_collection()

    for chunk in chunks[:10]:
        similar = search(chunk["text"], n_results=5)
        if not similar["ids"] or not similar["ids"][0]:
            continue
        for i, sid in enumerate(similar["ids"][0]):
            if paper_id in sid:
                continue
            try:
                resp = litellm.completion(
                    model="claude-3-haiku-20240307",
                    messages=[{"role": "user", "content": f"Compare statements:\nA: {similar['documents'][0][i][:400]}\nB: {chunk['text'][:300]}\nOutput JSON: {{\"relation\": \"agree|contradict|unrelated\", \"explanation\": \"why\"}}"}],
                    max_tokens=150, temperature=0,
                )
                result = json.loads(resp.choices[0].message.content.strip())
            except Exception:
                continue

            other_pid = similar["metadatas"][0][i].get("paper_id", "")
            other_idx = similar["metadatas"][0][i].get("chunk_index", 0)

            if result.get("relation") == "agree":
                _append_verified_source(other_pid, other_idx, paper_id, chunk["chunk_index"])
                _append_verified_source(paper_id, chunk["chunk_index"], other_pid, other_idx)
            elif result.get("relation") == "contradict":
                db.execute(
                    "INSERT OR IGNORE INTO chunk_conflicts (paper_id_a, chunk_index_a, paper_id_b, description) VALUES (?, ?, ?, ?)",
                    (paper_id, chunk["chunk_index"], other_pid, result.get("explanation", "")),
                )
                db.commit()


def _append_verified_source(target_paper_id: str, target_chunk_idx: int,
                             source_paper_id: str, source_chunk_idx: int):
    coll = get_collection()
    cid = f"{target_paper_id}_chunk_{target_chunk_idx}"
    try:
        existing = coll.get(ids=[cid])
        if not existing["metadatas"]:
            return
        current_meta = existing["metadatas"][0]
        sources_raw = current_meta.get("verified_sources", "[]")
        sources = json.loads(sources_raw) if isinstance(sources_raw, str) else sources_raw
        if not isinstance(sources, list):
            sources = []
        new_source = {"paper_id": source_paper_id, "chunk_index": source_chunk_idx}
        if new_source not in sources:
            sources.append(new_source)
        updated_meta = {**current_meta, "verified_sources": json.dumps(sources)}
        coll.update(ids=[cid], metadatas=[updated_meta])
    except Exception:
        pass


def _ingest_text(text: str, meta: Paper) -> tuple[Paper | None, str]:
    existing = deduplicate_by_title(meta.title)
    if existing:
        return None, "标题重复，已跳过"

    ok, reason = _should_accept(meta)
    if not ok:
        return None, reason

    paper_id = insert_paper(meta)
    meta.id = paper_id

    chunks = _chunk_text_with_sections(text)
    add_chunks(paper_id, chunks)

    _detect_and_merge_sources(paper_id, chunks)
    return meta, "摄入成功"


def ingest_pdf(file_path: str) -> tuple[Paper | None, str]:
    text = _parse_pdf(file_path)
    if not text:
        return None, "PDF解析失败或为空"
    fname = Path(file_path).stem
    meta = Paper(title=fname, file_path=file_path)
    _score_source(meta)
    return _ingest_text(text, meta)


def ingest_text(text: str, title: str, **metadata) -> tuple[Paper | None, str]:
    meta = Paper(title=title, file_path=metadata.get("file_path", ""), **metadata)
    _score_source(meta)
    return _ingest_text(text, meta)


def recall_full_paper(paper_id: str) -> str:
    coll = get_collection()
    results = coll.get(where={"paper_id": paper_id})
    if not results["ids"]:
        return ""
    pairs = list(zip(
        [m["chunk_index"] for m in results["metadatas"]],
        results["documents"],
    ))
    pairs.sort(key=lambda x: x[0])
    return "\n".join(text for _, text in pairs)