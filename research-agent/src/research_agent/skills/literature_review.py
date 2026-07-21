"""Literature Review Skill — orchestrates search, read, synthesize, write."""
from research_agent.tools.schema import ToolSchema, ToolResult


def _handle_literature_review(params: dict, llm, state, emit) -> ToolResult:
    """Orchestrate a full literature review workflow."""
    topic = params.get("topic", state.user_input if state else "")
    if not topic:
        return ToolResult.fail("Missing topic parameter")

    emit("step", {"step": "review_start", "text": f"Starting literature review: {topic[:60]}"})
    emit("plan", {"items": [
        "Search local knowledge base",
        "Search arXiv for latest papers",
        "Read full papers and extract contributions",
        "Synthesize into structured review",
        "Record references and notes",
    ]})

    papers_data = []

    # Step 1: Search local
    emit("step", {"step": "review_search", "text": "Searching local knowledge base..."})
    from research_agent.tools.builtin.retrieve import _handle_retrieve, _handle_search
    from research_agent.tools.builtin.filesystem import _handle_shell_exec

    local = _handle_retrieve({"query": topic}, llm, state, emit)
    if local.success and local.chunks:
        papers_data.extend(local.chunks)
        emit("step", {"step": "review_local", "text": f"Found {len(local.chunks)} local results"})

    # Step 2: Search arXiv
    emit("step", {"step": "review_arxiv", "text": "Searching arXiv..."})
    arxiv = _handle_search({"query": topic}, llm, state, emit)
    if arxiv.success and arxiv.chunks:
        papers_data.extend(arxiv.chunks)
        emit("step", {"step": "review_arxiv_done", "text": f"Found {arxiv.data.get('ingested', 0)} new papers"})

    if not papers_data:
        return ToolResult.fail(f"No papers found for topic: {topic}")

    # Step 3: Read top papers and extract contributions
    emit("step", {"step": "review_read", "text": f"Reading {min(len(papers_data), 8)} papers..."})
    paper_summaries = []
    seen_ids = set()

    for chunk in papers_data[:8]:
        pid = chunk.get("paper_id", "")
        if not pid or pid in seen_ids:
            continue
        seen_ids.add(pid)

        from research_agent.tools.builtin.retrieve import _handle_read_paper
        paper_result = _handle_read_paper({"paper_id": pid}, llm, state, emit)

        if paper_result.success and paper_result.data.get("full_text"):
            full_text = paper_result.data["full_text"]
            # Extract title and key contributions
            title = ""
            for line in full_text.split("\n")[:3]:
                if line.startswith("Title:") or line.startswith("# "):
                    title = line.replace("Title:", "").replace("#", "").strip()
                    break
            if not title:
                # Try to get from chunk text
                for line in chunk.get("text", "").split("\n"):
                    if line.startswith("Title:"):
                        title = line[6:].strip()
                        break

            paper_summaries.append({
                "paper_id": pid,
                "title": title or "Unknown",
                "content": full_text[:2000],
            })

    if not paper_summaries:
        return ToolResult.ok(review="Found papers but failed to read any. Try manually.", papers=[])

    # Step 4: Synthesize review
    emit("step", {"step": "review_synthesize", "text": "Synthesizing review..."})
    papers_text = "\n\n".join([
        f"### [{i+1}] {p['title']}\n{p['content'][:800]}"
        for i, p in enumerate(paper_summaries)
    ])

    sys = "你是一个研究综述写作助手。请基于以下论文内容写一篇结构化的综述。"
    prompt = f"""{sys}

主题: {topic}

基于以下 {len(paper_summaries)} 篇论文的内容，写一篇结构化综述。要求：
1. 概述该领域的研究现状
2. 按方法或方向分类讨论（2-4 个类别）
3. 每个类别引用具体论文的贡献
4. 总结研究趋势和未来方向
5. 最后列出所有参考文献（编号、标题、作者、年份、arXiv ID）

论文内容:
{papers_text}"""

    review = llm.complete(
        [{"role": "user", "content": prompt}],
        max_tokens=2500,
    )

    # Step 5: Save review as note
    from research_agent.tools.builtin.retrieve import _handle_update_notes
    _handle_update_notes({"notes": f"[综述] {topic}: 基于 {len(paper_summaries)} 篇论文完成综述（共 {len(review)} 字符）"}, llm, state, emit)

    # Also save review to workspace
    try:
        from research_agent.tools.builtin.filesystem import _get_project_dir
        import os
        ws = _get_project_dir(state)
        review_dir = os.path.join(ws, "reviews")
        os.makedirs(review_dir, exist_ok=True)
        safe_name = topic.replace("/", "_").replace("\\", "_").replace(":", "_")[:40]
        review_path = os.path.join(review_dir, f"{safe_name}.md")
        with open(review_path, "w", encoding="utf-8") as f:
            f.write(f"# Literature Review: {topic}\n\n{review}")
        emit("step", {"step": "review_saved", "text": f"Review saved: reviews/{safe_name}.md"})
    except Exception:
        pass

    return ToolResult.ok(
        review=review,
        papers=[{"title": p["title"], "paper_id": p["paper_id"]} for p in paper_summaries],
        total_papers=len(paper_summaries),
    )


literature_review_skill = ToolSchema(
    name="literature_review",
    description="【写综述必须用这个】自动搜索论文→阅读全文→提取贡献→生成结构化综述→列出参考文献→保存到工作区。不要绕过它用 retrieve+generate 拼凑。",
    parameters={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "综述主题"}
        },
        "required": ["topic"],
    },
    handler=_handle_literature_review,
    category="skill",
)