"""Built-in research tools (Tier A, grep mode).

Local retrieval is keyword/grep over workspace/papers/*.md (formal zone).
read_paper uses a two-phase landing: read into an isolated staging zone first;
only read_paper(paper_id, persist=True) promotes staging → formal so it becomes
grep-able long-term. No vector store / Chroma involved.
"""
import os

from research_agent.tools.schema import ToolSchema, ToolResult
from research_agent.retrieval import grep_papers
from research_agent import paper_store


def _workspace(state) -> str:
    from research_agent.tools.builtin.filesystem import _get_project_dir
    return _get_project_dir(state)


# ── retrieve: keyword/grep over formal papers ───────────────────────────────

def _handle_retrieve(params: dict, llm, state, emit) -> ToolResult:
    query = params.get("query", "")
    if not query.strip():
        return ToolResult.fail("Missing query parameter")

    emit("tool", {"tool": "retrieve", "status": "start", "query": query})
    papers_dir = paper_store.formal_papers_dir(_workspace(state))
    results = grep_papers(query, papers_dir, n_results=5)

    if results:
        emit("tool", {"tool": "retrieve", "status": "done", "count": len(results)})
        paper_ids = [r.get("paper_id", "") for r in results if r.get("paper_id")]
        items = [
            {
                "paper_id": r.get("paper_id", ""),
                "title": r.get("title", ""),
                "text_snippet": r.get("text", "")[:300],
            }
            for r in results
        ]
        return ToolResult(success=True, chunks=results, data={
            "found": len(results), "source": "local",
            "paper_ids": paper_ids, "items": items,
        })

    emit("tool", {"tool": "retrieve", "status": "local_empty", "query": query})
    return ToolResult(success=False, data={
        "found": 0, "source": "local",
        "hint": "本地正式区无匹配。可用 search_papers 搜 arXiv；或用 read_paper(paper_id, persist=true) 落地后再检索。",
    })


# ── search_papers: arXiv metadata only (never lands files) ──────────────────

def _handle_search(params: dict, llm, state, emit) -> ToolResult:
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

    readable = []
    for i, r in enumerate(results):
        readable.append(
            f"[{i+1}] {r['title']}\n"
            f"    ID: {r['paper_id']} | {r['year']} | {', '.join(r['authors'][:3])}\n"
            f"    {r['abstract']}"
        )
    result_text = "检索到以下论文:\n\n" + "\n\n".join(readable)
    first_pid = results[0]['paper_id'] if results else ''
    result_text += (
        f"\n\n下一步: 选中论文后用 read_paper(paper_id=\"{first_pid}\") 读全文。"
        f"默认只读入临时区（不持久化）；确认论文有用后，再调用 "
        f"read_paper(paper_id=\"{first_pid}\", persist=true) 正式落地，之后可用 retrieve 搜到。"
    )

    return ToolResult.ok(
        found=len(results),
        papers=results,
        _formatted=result_text,
        hint=f"用 read_paper(paper_id=\"{first_pid}\") 读全文；确认有用后加 persist=true 落地",
    )


# ── read_paper: two-phase landing (staging → formal) ────────────────────────

def _meta_from_result(paper_id: str, meta: dict | None) -> dict:
    return {
        "id": paper_id,
        "title": (meta or {}).get("title", ""),
        "authors": (meta or {}).get("authors", []),
        "year": (meta or {}).get("year", 0),
        "abstract": (meta or {}).get("abstract", ""),
        "doi": (meta or {}).get("doi", f"arxiv:{paper_id}"),
        "source": (meta or {}).get("source", "arxiv"),
    }


def _read_local_md(path: str) -> dict | None:
    return paper_store.read_md(path)


def _handle_read_paper(params: dict, llm, state, emit) -> ToolResult:
    pid = params.get("paper_id", "")
    persist = bool(params.get("persist", False))
    if not pid:
        return ToolResult.fail("Missing paper_id")

    ws = _workspace(state)
    emit("tool", {"tool": "read_paper", "status": "start", "paper_id": pid,
                  "persist": persist})

    # 1. Formal zone already has it → return (idempotent).
    formal = _read_local_md(paper_store.formal_path(ws, pid))
    if formal:
        emit("tool", {"tool": "read_paper", "status": "found", "zone": "formal",
                      "paper_id": pid})
        _note_read(state, ws, pid, formal, persisted=True)
        return _build_read_result(pid, formal, ws, "正式库")

    # 2. Staging zone has it → promote if persist=True, else read-only temp.
    staged = _read_local_md(paper_store.staging_path(ws, pid))
    if staged:
        if persist:
            promoted = paper_store.promote_to_formal(ws, pid)
            if promoted:
                formal = _read_local_md(promoted)
                emit("tool", {"tool": "read_paper", "status": "promoted",
                              "paper_id": pid, "to": "formal"})
                _note_read(state, ws, pid, formal or staged, persisted=True)
                return _build_read_result(pid, formal or staged, ws, "正式库(已晋升)")
            return ToolResult.fail("无法晋升到正式区")
        emit("tool", {"tool": "read_paper", "status": "found", "zone": "staging",
                      "paper_id": pid})
        return _build_read_result(pid, staged, ws, "临时区(未持久化)",
                                  hint="确认有用请调用 read_paper(paper_id, persist=true) 正式落地")

    # 3. Not local → fetch metadata + full text from arXiv, land into zone.
    emit("tool", {"tool": "read_paper", "status": "fetching", "paper_id": pid})
    try:
        from research_agent.search import get_paper_metadata
        meta = get_paper_metadata(pid, id_type="arxiv")
    except Exception as e:
        meta = None
        return ToolResult.fail(f"Failed to fetch metadata: {e}")
    if not meta:
        return ToolResult.fail(f"Cannot fetch paper: {pid}（本地无此论文，且 arXiv 无法解析该 ID）")

    from research_agent.tools.arxiv_pdf import fetch_pdf_text
    full_text, err = fetch_pdf_text(pid)
    text = full_text or (meta.get("abstract") or "")
    if not text:
        return ToolResult.fail(f"PDF 全文与摘要均为空: {err or 'unknown'}")
    if not full_text:
        emit("tool", {"tool": "read_paper", "status": "abstract_only",
                      "paper_id": pid, "reason": err or "PDF unavailable"})

    zone = "formal" if persist else "staging"
    mm = _meta_from_result(pid, meta)
    body = text if full_text else f"{meta.get('abstract', '')}\n\n[注] 完整 PDF 未能获取，仅摘要可用。"
    if persist:
        path = paper_store.write_md(paper_store.formal_path(ws, pid), mm, body)
        emitted = {"tool": "read_paper", "status": "ingested", "paper_id": pid, "zone": "formal"}
    else:
        path = paper_store.write_md(paper_store.staging_path(ws, pid), mm, body)
        emitted = {"tool": "read_paper", "status": "staged", "paper_id": pid,
                   "zone": "staging",
                   "hint": "未持久化。确认有用后调用 read_paper(paper_id, persist=true)"}
    emit("tool", emitted)
    entry = _read_local_md(path)
    _note_read(state, ws, pid, entry or {"meta": mm, "text": body}, persisted=persist)
    if persist:
        emit("file_change", {"tool_id": getattr(state, '_current_tool_id', 'unknown'),
                             "path": f"papers/{os.path.basename(path)}", "action": "create"})
    return _build_read_result(pid, entry or {"meta": mm, "text": body}, ws,
                              "正式库(已下载全文)" if persist else
                              ("临时区(摘要,PDF不可用)" if not full_text else "临时区(已下载全文)"),
                              hint=None if persist else "确认有用请调 read_paper(paper_id, persist=true) 正式落地")


def _note_read(state, ws: str, pid: str, entry: dict, persisted: bool):
    """Only persisted reads become a project note (avoid polluting on temp reads)."""
    if not persisted:
        return
    try:
        proj = getattr(state, 'active_project', None)
        title = (entry.get("meta") or {}).get("title", "") or pid
        year = (entry.get("meta") or {}).get("year", 0)
        note_entry = f"[read] {title} ({year})"
        existing = proj.progress_text or ""
        proj.progress_text = existing + "\n" + note_entry if existing else note_entry
        if ws:
            from research_agent import project_manager as pm
            pm.update_progress(ws, proj.progress_text)
    except Exception:
        pass


def _build_read_result(pid: str, entry: dict, ws: str, source: str = "",
                       hint: str | None = None) -> ToolResult:
    """Build read_paper ToolResult from a paper md entry."""
    meta = entry.get("meta") or {}
    text = entry.get("text") or ""
    if not text:
        text = "(empty)"
    truncated = text[:30000]

    data = {
        "paper_id": pid,
        "title": meta.get("title", ""),
        "authors": (meta.get("authors") or [])[:5],
        "year": meta.get("year", 0),
        "doi": meta.get("doi", ""),
        "full_text": truncated,
        "length": len(truncated),
        "source": source,
        "persisted": source.startswith("正式") or source == "正式库",
    }
    if hint:
        data["hint"] = hint
    return ToolResult.ok(**data)


# ── delete_paper / update_notes ─────────────────────────────────────────────

def _handle_delete_paper(params: dict, llm, state, emit) -> ToolResult:
    pid = params.get("paper_id", "")
    if not pid:
        return ToolResult.fail("Missing paper_id")
    from research_agent.tools.arxiv_pdf import delete_paper_files
    removed = delete_paper_files(_workspace(state), pid)
    if removed:
        emit("tool", {"tool": "delete_paper", "status": "done", "paper_id": pid})
        return ToolResult.ok(deleted=pid, message="已删除正式/临时论文文件")
    return ToolResult.fail(f"本地没有这篇论文: {pid}")


def _handle_update_notes(params: dict, llm, state, emit) -> ToolResult:
    notes = params.get("notes", "")
    if not notes.strip():
        return ToolResult.fail("Missing notes")
    from research_agent import project_manager as pm
    proj = getattr(state, 'active_project', None)
    if not proj:
        return ToolResult.fail("No active project")
    ws = getattr(state, 'workspace_dir', '')
    from datetime import datetime
    ts = datetime.now().strftime("%H:%M")
    existing = proj.progress_text or ""
    new_notes = existing + "\n" + f"[{ts}] {notes}" if existing else f"[{ts}] {notes}"
    proj.progress_text = new_notes
    if ws:
        pm.update_progress(ws, new_notes)
    return ToolResult.ok(entry=f"[{ts}] {notes}", count=new_notes.count("\n") + 1)


# ── Tool Definitions ────────────────────────────────────────────────────────

retrieve_tool = ToolSchema(
    name="retrieve",
    description=(
        "搜索本地正式论文库（workspace/papers/*.md），关键词/grep 匹配。"
        "仅搜已持久化论文，不搜互联网。如果 found=0，改用 search_papers 去 arXiv 搜索。"
        "注意：search_papers 返回的论文尚未落地，不能用 retrieve 找——先 read_paper 读，"
        "确认有用后 read_paper(persist=true) 落地才能被 retrieve 检索到。"
    ),
    parameters={"type": "object",
                "properties": {"query": {"type": "string", "description": "搜索关键词"}},
                "required": ["query"]},
    handler=_handle_retrieve, category="builtin",
)

search_tool = ToolSchema(
    name="search_papers",
    description=(
        "在 arXiv 搜索最新论文，返回标题+摘要列表（只搜不落地，不写任何文件）。"
        "找到论文后用 read_paper(paper_id='...') 读全文；确认有用后再以 "
        "read_paper(paper_id='...', persist=true) 正式落地。每次运行最多调用 2 次。"
    ),
    parameters={"type": "object",
                "properties": {"query": {"type": "string", "description": "英文搜索关键词"}},
                "required": ["query"]},
    handler=_handle_search, category="builtin",
)

read_paper_tool = ToolSchema(
    name="read_paper",
    description=(
        "读论文全文。传入 search_papers 返回的 arXiv ID。"
        "默认(persist=false)只下载到隔离临时区读取，不持久化、不被 retrieve 检索到；"
        "只有当论文确认有用后，再次调用 read_paper(paper_id, persist=true) 才会正式落地到本地，之后可被 retrieve 搜到。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "paper_id": {"type": "string", "description": "论文 ID（arXiv ID）"},
            "persist": {"type": "boolean",
                        "description": "是否正式落地。true=晋升为本地正式论文(可被 retrieve 检索)；默认 false=仅临时读取"},
        },
        "required": ["paper_id"],
    },
    handler=_handle_read_paper, category="builtin",
)

delete_paper_tool = ToolSchema(
    name="delete_paper",
    description="删除本地论文（正式区 + 临时区文件）。",
    parameters={"type": "object",
                "properties": {"paper_id": {"type": "string", "description": "论文 ID"}},
                "required": ["paper_id"]},
    handler=_handle_delete_paper, category="builtin",
)

update_notes_tool = ToolSchema(
    name="update_notes",
    description="记录研究发现、修正之前的理解。每次实验或阅读后有值得记录的结论时主动调用。",
    parameters={"type": "object", "properties": {"notes": {"type": "string", "description": "笔记内容"}},
                "required": ["notes"]},
    handler=_handle_update_notes, category="builtin",
)
