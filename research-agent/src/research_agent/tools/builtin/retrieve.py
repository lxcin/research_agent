"""Built-in tools: local retrieval and arXiv search."""
import os
from research_agent.tools.schema import ToolSchema, ToolResult
from research_agent.retrieval import hybrid_search, build_bm25_index
from research_agent.ingestion import ingest_text, deduplicate_by_title


def _save_paper_workspace(state, meta: dict, paper):
    """Save paper markdown to workspace papers/ directory."""
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
                f"# {meta.get('title', '')}\n\n"
                f"**Authors**: {', '.join(meta.get('authors', []))}\n"
                f"**Year**: {meta.get('year', '')}\n"
                f"**arXiv**: {meta.get('arxiv_id', '')}\n\n"
                f"## Abstract\n{meta.get('abstract', '')}\n"
            )
    except Exception:
        pass


def _link_to_project(state, paper):
    """Link ingested paper to active project."""
    try:
        pid_obj = getattr(state, 'active_project', None)
        if pid_obj and getattr(pid_obj, 'id', None):
            from research_agent.store import link_paper_to_project
            link_paper_to_project(paper.id, pid_obj.id)
    except Exception:
        pass


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
        # Extract title/snippet per chunk so LLM can decide which papers to read
        items = []
        seen = set()
        for c in results:
            pid = c.get("paper_id", "")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            items.append({
                "paper_id": pid,
                "text_snippet": c.get("text", "")[:200],
            })
        return ToolResult(success=True, chunks=results, data={
            "found": len(results), "source": "local",
            "paper_ids": paper_ids, "items": items,
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

    # Format as readable text for tool result message (LLM sees this)
    readable = []
    for i, r in enumerate(results):
        readable.append(
            f"[{i+1}] {r['title']}\n"
            f"    ID: {r['paper_id']} | {r['year']} | {', '.join(r['authors'][:3])}\n"
            f"    {r['abstract']}"
        )
    result_text = "检索到以下论文:\n\n" + "\n\n".join(readable)
    result_text += f"\n\n下一步: 选择一篇论文，用 read_paper(paper_id=\"{results[0]['paper_id'] if results else ''}\") 读全文。不要用 retrieve——retrieve 搜本地，搜不到刚找到的 arXiv 论文。"

    return ToolResult.ok(
        found=len(results),
        papers=results,
        _formatted=result_text,
        hint="用 read_paper(paper_id=\"...\") 读全文，不用 retrieve",
    )


retrieve_tool = ToolSchema(
    name="retrieve",
    description=(
        "搜索本地知识库中已读过的论文。仅搜本地，不搜互联网。"
        "如果 found=0 或结果不足，改用 search_papers 去 arXiv 搜索。"
        "注意：search_papers 返回的论文不在本地，不能用 retrieve 找——直接用 read_paper(paper_id) 读。"
    ),
    parameters={"type": "object", "properties": {"query": {"type": "string", "description": "搜索关键词"}}, "required": ["query"]},
    handler=_handle_retrieve, category="builtin",
)

search_tool = ToolSchema(
    name="search_papers",
    description=(
        "在 arXiv 搜索最新论文，返回摘要列表（不自动存入本地）。"
        "找到论文后，直接用 read_paper(paper_id='...') 读取全文——不要用 retrieve。"
        "每次运行最多调用 2 次。"
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

    # 1. Try existing full text from ChromaDB (idempotent)
    text = recall_full_paper(pid)
    if text:
        emit("tool", {"tool": "read_paper", "status": "found", "paper_id": pid})
        return _build_read_result(pid, text, state, "全文 (本地)")

    # 2. Fetch metadata + abstract from arXiv (fast, synchronous)
    emit("tool", {"tool": "read_paper", "status": "fetching", "paper_id": pid})
    try:
        from research_agent.search import get_paper_metadata
        meta = get_paper_metadata(pid, id_type="arxiv")
        if not meta or not meta.get("abstract"):
            return ToolResult.fail(f"Cannot fetch paper: {pid}")
    except Exception as e:
        return ToolResult.fail(f"Failed to fetch metadata: {e}")

    title = meta.get("title", "")
    abstract = meta.get("abstract", "")

    # 3. Check dedup, then ingest abstract immediately
    existing = deduplicate_by_title(title)
    if existing:
        text = recall_full_paper(existing.id) or abstract
    else:
        ingest_body = (
            f"Title: {title}\n"
            f"Authors: {', '.join(meta.get('authors', []))}\n"
            f"Year: {meta.get('year', '')}\n\n"
            f"Abstract: {abstract}"
        )
        paper, msg = ingest_text(
            text=ingest_body, title=title,
            doi=f"arxiv:{meta.get('arxiv_id', pid)}",
            year=meta.get("year", 0), authors=meta.get("authors", []),
            abstract=abstract,
        )
        if paper:
            emit("tool", {"tool": "ingest", "status": "abstract_ingested", "title": title[:80]})
            _save_paper_workspace(state, meta, paper)
            _link_to_project(state, paper)
        text = ingest_body

    # 4. Kick off async PDF download for full text
    emit("tool", {"tool": "read_paper", "status": "pdf_downloading",
           "hint": "PDF downloading in background, available next read"})
    try:
        import json as _json
        def _on_pdf_done(full_text):
            emit("tool", {"tool": "read_paper", "status": "pdf_ready",
                   "hint": f"PDF fully ingested for {pid}"})

        from research_agent.tools.arxiv_pdf import ingest_arxiv_async
        ingest_arxiv_async(pid, meta, on_done=_on_pdf_done)
    except Exception:
        pass

    return _build_read_result(pid, text, state, f"摘要 (PDF下载中，下次阅读可得全文)")


def _build_read_result(pid: str, text: str, state, source: str = "") -> ToolResult:
    """Build read_paper ToolResult with metadata lookup."""
    if not text:
        text = "(empty)"
    words = text.split()
    truncated = " ".join(words[:4000]) if len(words) > 4000 else text

    title = ""; authors = []; year = 0; doi = ""
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
            note_entry = f"[read] {title[:80] or pid} ({year})"
            project.accumulated_wisdom.notes = existing + "\n" + note_entry if existing else note_entry
            update_project(project)
    except Exception:
        pass

    return ToolResult.ok(
        paper_id=pid, title=title, authors=authors[:5], year=year, doi=doi,
        full_text=truncated, length=len(truncated),
        source=source,
    )


read_paper_tool = ToolSchema(
    name="read_paper",
    description=(
        "读论文全文。传入 search_papers 返回的 arXiv ID，或 retrieve 查到的本地 ID 都可以。"
        "首次读取时自动下载 PDF 全文并摄入本地知识库，后续可被 retrieve 找到。"
    ),
    parameters={"type": "object", "properties": {"paper_id": {"type": "string", "description": "论文 ID（arXiv ID 或本地 ID）"}}, "required": ["paper_id"]},
    handler=_handle_read_paper, category="builtin",
)


def _handle_delete_paper(params: dict, llm, state, emit) -> ToolResult:
    """Delete a paper from the knowledge base."""
    pid = params.get("paper_id", "")
    if not pid:
        return ToolResult.fail("Missing paper_id")
    from research_agent.tools.arxiv_pdf import delete_paper_from_kb
    ok = delete_paper_from_kb(pid)
    if ok:
        return ToolResult.ok(deleted=pid, message="Paper removed from knowledge base")
    return ToolResult.fail(f"Failed to delete paper: {pid}")


delete_paper_tool = ToolSchema(
    name="delete_paper",
    description="从知识库中删除一篇论文（ChromaDB + SQLite + 工作区）。",
    parameters={"type": "object", "properties": {"paper_id": {"type": "string", "description": "论文 ID"}}, "required": ["paper_id"]},
    handler=_handle_delete_paper, category="builtin",
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