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
    query = params.get("query", "")
    if not query.strip():
        return ToolResult.fail("Missing query parameter")

    pid = getattr(state, 'active_project', None)
    project_id = getattr(pid, 'id', None) if pid else None

    from research_agent.search import search_papers

    emit("tool", {"tool": "arxiv", "status": "start", "query": query})
    papers = search_papers(query, limit=5)

    if not papers:
        emit("tool", {"tool": "arxiv", "status": "empty", "query": query})
        return ToolResult(success=False, data={"found": 0})

    emit("tool", {"tool": "arxiv", "status": "found", "count": len(papers),
           "papers": [{"title": p["title"], "year": p.get("year", 0)} for p in papers]})

    if llm and len(papers) > 1:
        papers_list = "\n".join([f"{i+1}. [{p['title']}] {p.get('abstract', '')[:200]}" for i, p in enumerate(papers)])
        filter_prompt = f"判断以下论文是否与查询\"{query}\"相关。只输出相关的论文编号（逗号分隔），如 \"1,3,5\"。全部不相关输出 \"none\"。\n{papers_list}\n输出："
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

    ingested = 0
    ingested_papers = []
    for p in papers:
        if not p.get("abstract"):
            continue
        existing = deduplicate_by_title(p["title"])
        if existing:
            emit("tool", {"tool": "ingest", "status": "duplicate", "title": p["title"]})
            continue

        text = (f"Title: {p['title']}\nAuthors: {', '.join(p.get('authors', []))}\n"
                f"Year: {p.get('year', '')}\nVenue: {p.get('venue', '')}\n\nAbstract: {p['abstract']}")
        emit("tool", {"tool": "ingest", "status": "ingesting", "title": p["title"]})
        paper, msg = ingest_text(
            text=text, title=p["title"], doi=f"arxiv:{p.get('arxiv_id', '')}",
            year=p.get("year", 0), authors=p.get("authors", []),
            citation_count=p.get("citation_count", 0), abstract=p.get("abstract", ""),
        )
        if paper:
            ingested += 1
            ingested_papers.append(p)
            pid_obj = getattr(state, 'active_project', None)
            if pid_obj and getattr(pid_obj, 'id', None):
                from research_agent.store import link_paper_to_project
                link_paper_to_project(paper.id, pid_obj.id)
            if llm:
                try:
                    from research_agent.knowledge_graph import build_global_graph_on_ingest
                    import threading
                    t = threading.Thread(target=build_global_graph_on_ingest, args=(paper.id, text, llm), daemon=True)
                    t.start()
                except Exception:
                    pass
            try:
                from research_agent.tools.builtin.filesystem import _get_project_dir
                ws = _get_project_dir(state)
                papers_dir = os.path.join(ws, "papers")
                if not os.path.exists(papers_dir): os.makedirs(papers_dir)
                safe_name = p.get("arxiv_id", paper.id).replace("/", "_").replace("\\", "_")
                md_path = os.path.join(papers_dir, f"{safe_name}.md")
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(f"# {p['title']}\n\n**Authors**: {', '.join(p.get('authors', []))}\n"
                            f"**Year**: {p.get('year', '')}\n**arXiv**: {p.get('arxiv_id', '')}\n"
                            f"**Venue**: {p.get('venue', '')}\n\n## Abstract\n{p.get('abstract', '')}\n")
                emit("tool", {"tool": "ingest", "status": "file_saved", "title": p["title"]})
            except Exception:
                pass
        else:
            emit("tool", {"tool": "ingest", "status": "error", "title": p["title"], "error": msg[:80]})

    if ingested == 0:
        emit("tool", {"tool": "ingest", "status": "failed", "reason": "no new papers ingested"})
        return ToolResult(success=False, data={"found": 0, "ingested": 0})

    emit("tool", {"tool": "ingest", "status": "done", "count": ingested})
    build_bm25_index()
    results = hybrid_search(query, n_results=5, project_id=project_id)
    emit("tool", {"tool": "retrieve", "status": "done", "chunks": len(results)})

    titles = [p["title"] for p in ingested_papers]
    paper_ids = list({c.get("paper_id", "") for c in results if c.get("paper_id")})
    return ToolResult(success=True, chunks=results, data={
        "found": len(papers), "ingested": ingested,
        "titles": titles, "chunks": len(results), "paper_ids": paper_ids,
    })


retrieve_tool = ToolSchema(
    name="retrieve",
    description="搜索本地论文知识库。先搜本地，无结果或不足时考虑 search_papers。",
    parameters={"type": "object", "properties": {"query": {"type": "string", "description": "搜索关键词"}}, "required": ["query"]},
    handler=_handle_retrieve, category="builtin",
)

search_tool = ToolSchema(
    name="search_papers",
    description="在 arXiv 搜索最新论文并自动摄入知识库。本地检索无结果或不足时使用。",
    parameters={"type": "object", "properties": {"query": {"type": "string", "description": "英文搜索关键词"}}, "required": ["query"]},
    handler=_handle_search, category="builtin",
)


def _handle_read_paper(params: dict, llm, state, emit) -> ToolResult:
    from research_agent.ingestion import recall_full_paper
    pid = params.get("paper_id", "")
    if not pid: return ToolResult.fail("Missing paper_id")
    emit("tool", {"tool": "read_paper", "status": "start", "paper_id": pid})
    text = recall_full_paper(pid)
    if not text: return ToolResult.fail("Paper not found")
    words = text.split()
    truncated = " ".join(words[:4000]) if len(words) > 4000 else text

    # Get structured metadata from ChromaDB summary doc
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
    description="读取已摄入论文的完整内容。用于深度理解论文细节。",
    parameters={"type": "object", "properties": {"paper_id": {"type": "string", "description": "论文 ID"}}, "required": ["paper_id"]},
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