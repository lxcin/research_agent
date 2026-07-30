"""Built-in tools: local retrieval and arXiv search."""
import os
from research_agent.tools.schema import ToolSchema, ToolResult
from research_agent.retrieval import hybrid_search, build_bm25_index
from research_agent.ingestion import ingest_text, deduplicate_by_title


def _handle_retrieve(params: dict, llm, state, emit) -> ToolResult:
    query = params.get("query", "")
    if not query.strip():
        return ToolResult.fail("Missing query parameter")

    pid = getattr(state, 'active_project', None)
    project_id = getattr(pid, 'id', None) if pid else None

    emit("tool", {"tool": "retrieve", "status": "start", "query": query})
    build_bm25_index()
    results = hybrid_search(query, n_results=5, project_id=project_id)

    if results:
        emit("tool", {"tool": "retrieve", "status": "done", "chunks": len(results)})
        paper_ids = list({c.get("paper_id", "") for c in results if c.get("paper_id")})
        return ToolResult(success=True, chunks=results, data={
            "found": len(results), "source": "local", "paper_ids": paper_ids,
        })

    emit("tool", {"tool": "retrieve", "status": "local_empty", "query": query})
    return ToolResult(success=False, data={"found": 0, "source": "local"})


def _handle_search(params: dict, llm, state, emit) -> ToolResult:
    """Search arXiv for papers. Returns metadata only — no auto-ingestion.
    LLM must call read_paper to ingest specific papers into the knowledge base."""
    query = params.get("query", "")
    if not query.strip():
        return ToolResult.fail("Missing query parameter")

    from research_agent.search import search_papers

    emit("tool", {"tool": "arxiv", "status": "start", "query": query})
    papers = search_papers(query, limit=5)

    if not papers:
        emit("tool", {"tool": "arxiv", "status": "empty", "query": query})
        return ToolResult.fail("No papers found on arXiv")

    emit("tool", {"tool": "arxiv", "status": "found", "count": len(papers),
           "papers": [{"title": p["title"], "year": p.get("year", 0)} for p in papers]})

    # Optional LLM relevance filter
    if llm and len(papers) > 1:
        papers_list = "\n".join(
            f"{i+1}. [{p['title']}] {p.get('abstract', '')[:200]}"
            for i, p in enumerate(papers)
        )
        filter_prompt = (
            f"判断以下论文是否与查询\"{query}\"相关。"
            f"只输出相关的论文编号（逗号分隔），如 \"1,3,5\"。全部不相关输出 \"none\"。\n"
            f"{papers_list}\n输出："
        )
        try:
            raw = llm.complete([{"role": "user", "content": filter_prompt}], max_tokens=50)
            raw = raw.strip()
            if raw.lower() != "none":
                indices = [int(x.strip()) - 1 for x in raw.split(",") if x.strip().isdigit()]
                indices = [i for i in indices if 0 <= i < len(papers)]
                if indices:
                    papers = [papers[i] for i in indices]
                    emit("tool", {"tool": "arxiv", "status": "filtered", "count": len(papers)})
        except Exception:
            pass

    # Return search results as data — NO ingestion
    # LLM decides which papers to read; read_paper handles ingestion
    results = []
    for p in papers:
        results.append({
            "paper_id": p.get("arxiv_id", ""),
            "title": p.get("title", ""),
            "authors": p.get("authors", [])[:5],
            "year": p.get("year", 0),
            "abstract": p.get("abstract", "")[:500],
            "source": "arxiv",
        })

    return ToolResult.ok(
        found=len(results),
        papers=results,
        hint="调用 read_paper 摄入并阅读感兴趣的论文。",
    )


retrieve_tool = ToolSchema(
    name="retrieve",
    description="搜索本地论文知识库。先搜本地，无结果或不足时考虑 search_papers。",
    parameters={"type": "object", "properties": {"query": {"type": "string", "description": "搜索关键词"}}, "required": ["query"]},
    handler=_handle_retrieve, category="builtin",
)

search_tool = ToolSchema(
    name="search_papers",
    description=(
        "在 arXiv 搜索最新论文，返回论文列表（标题、摘要、作者、年份）。"
        "不会自动摄入知识库——需要读哪篇请用 read_paper，首次读取时自动摄入。"
    ),
    parameters={"type": "object", "properties": {"query": {"type": "string", "description": "英文搜索关键词"}}, "required": ["query"]},
    handler=_handle_search, category="builtin",
)


def _handle_read_paper(params: dict, llm, state, emit) -> ToolResult:
    from research_agent.ingestion import recall_full_paper, ingest_text, deduplicate_by_title
    pid = params.get("paper_id", "")
    if not pid:
        return ToolResult.fail("Missing paper_id")

    emit("tool", {"tool": "read_paper", "status": "start", "paper_id": pid})
    text = recall_full_paper(pid)

    # If not in local DB, fetch from arXiv and ingest on first read
    if not text:
        emit("tool", {"tool": "read_paper", "status": "fetching", "paper_id": pid})
        try:
            from research_agent.search import get_paper_metadata
            meta = get_paper_metadata(pid, id_type="arxiv")
            if meta and meta.get("abstract"):
                # Check dedup before ingesting
                existing = deduplicate_by_title(meta["title"])
                if not existing:
                    ingest_body = (
                        f"Title: {meta['title']}\n"
                        f"Authors: {', '.join(meta.get('authors', []))}\n"
                        f"Year: {meta.get('year', '')}\n\n"
                        f"Abstract: {meta['abstract']}"
                    )
                    paper, msg = ingest_text(
                        text=ingest_body,
                        title=meta["title"],
                        doi=f"arxiv:{meta.get('arxiv_id', pid)}",
                        year=meta.get("year", 0),
                        authors=meta.get("authors", []),
                        abstract=meta.get("abstract", ""),
                    )
                    if paper:
                        emit("tool", {"tool": "ingest", "status": "auto_ingested",
                               "title": meta["title"][:80]})
                        # Link to project
                        pid_obj = getattr(state, 'active_project', None)
                        if pid_obj and getattr(pid_obj, 'id', None):
                            from research_agent.store import link_paper_to_project
                            link_paper_to_project(paper.id, pid_obj.id)
                        # Save workspace markdown
                        try:
                            from research_agent.tools.builtin.filesystem import _get_project_dir
                            ws = _get_project_dir(state)
                            papers_dir = os.path.join(ws, "papers")
                            if not os.path.exists(papers_dir):
                                os.makedirs(papers_dir)
                            safe_name = meta.get("arxiv_id", paper.id).replace("/", "_").replace("\\", "_")
                            md_path = os.path.join(papers_dir, f"{safe_name}.md")
                            with open(md_path, "w", encoding="utf-8") as f:
                                f.write(
                                    f"# {meta['title']}\n\n"
                                    f"**Authors**: {', '.join(meta.get('authors', []))}\n"
                                    f"**Year**: {meta.get('year', '')}\n"
                                    f"**arXiv**: {meta.get('arxiv_id', pid)}\n\n"
                                    f"## Abstract\n{meta['abstract']}\n"
                                )
                        except Exception:
                            pass
                    text = ingest_body
                else:
                    text = recall_full_paper(existing.id) or meta.get("abstract", "")
            else:
                return ToolResult.fail(f"Cannot fetch paper: {pid}")
        except Exception as e:
            return ToolResult.fail(f"Failed to fetch paper: {e}")

    if not text:
        return ToolResult.fail("Paper not found")

    words = text.split()
    truncated = " ".join(words[:4000]) if len(words) > 4000 else text

    # Get structured metadata
    title = ""
    authors = []
    year = 0
    doi = ""
    try:
        from research_agent.vector_store import get_collection
        coll = get_collection()
        result = coll.get(ids=[f"{pid}_summary"])
        if result and result["metadatas"]:
            m = result["metadatas"][0]
            title = m.get("title", "")
            authors_str = m.get("authors", "")
            authors = [a.strip() for a in authors_str.split(",")] if authors_str else []
            year = m.get("year", 0)
            doi = m.get("doi", "")
    except Exception:
        pass

    # Auto-save note
    try:
        from research_agent.store import get_project, update_project
        project = get_project(state.active_project.id) if state.active_project else None
        if project:
            existing = getattr(project.accumulated_wisdom, 'notes', "") or ""
            note_entry = f"[read] {title[:80] or pid} ({year})" if title else f"[read] paper {pid}"
            project.accumulated_wisdom.notes = existing + "\n" + note_entry if existing else note_entry
            update_project(project)
    except Exception:
        pass

    return ToolResult.ok(
        paper_id=pid,
        title=title,
        authors=authors[:5],
        year=year,
        doi=doi,
        full_text=truncated,
        length=len(truncated),
    )


read_paper_tool = ToolSchema(
    name="read_paper",
    description=(
        "读取论文完整内容用于深度理解。如果是 arXiv ID 且尚未摄入，"
        "会自动从 arXiv 获取并摄入知识库（首次读取时）。"
    ),
    parameters={"type": "object", "properties": {"paper_id": {"type": "string", "description": "论文 ID（arXiv ID 或本地 ID）"}}, "required": ["paper_id"]},
    handler=_handle_read_paper, category="builtin",
)


def _handle_update_notes(params: dict, llm, state, emit) -> ToolResult:
    notes = params.get("notes", "")
    if not notes.strip(): return ToolResult.fail("Missing notes")
    from research_agent.store import get_project, update_project
    project = get_project(state.active_project.id) if state.active_project else None
    if not project: return ToolResult.fail("No active project")
    from datetime import datetime
    ts = datetime.now().strftime("%H:%M")
    existing = getattr(project.accumulated_wisdom, 'notes', "") or ""
    new_notes = existing + "\n" + f"[{ts}] {notes}" if existing else f"[{ts}] {notes}"
    project.accumulated_wisdom.notes = new_notes
    update_project(project)
    return ToolResult.ok(entry=f"[{ts}] {notes}", count=new_notes.count("\n") + 1)


update_notes_tool = ToolSchema(
    name="update_notes",
    description="记录研究发现、修正之前的理解。每次实验或阅读后有值得记录的结论时主动调用。",
    parameters={"type": "object", "properties": {"notes": {"type": "string", "description": "笔记内容"}}, "required": ["notes"]},
    handler=_handle_update_notes, category="builtin",
)