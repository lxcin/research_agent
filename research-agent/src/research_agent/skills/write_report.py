import litellm
from research_agent.models import AgentState
from research_agent.skill import Skill
from research_agent.retrieval import hybrid_search, build_bm25_index


def _execute_write_report(state: AgentState) -> AgentState:
    query = state.user_input
    for kw in ["写报告", "生成报告", "write report", "报告"]:
        query = query.replace(kw, "").strip()
    if not query:
        query = state.active_project.topic if state.active_project else "research progress"

    build_bm25_index()
    results = hybrid_search(query, n_results=10)
    state.retrieved_chunks = results

    chunks_text = "\n".join([
        f"[{i+1}] {c['text'][:300]}"
        for i, c in enumerate(results[:5])
    ]) if results else "No relevant documents found."

    prompt = f"""You are a research report writing assistant. Generate a professional research report based on the context below.

Topic: {query}

Context:
{chunks_text}

Write a structured research report in Chinese. Include:
1. 摘要 (Abstract)
2. 引言 (Introduction)
3. 方法 (Methods)
4. 结果与讨论 (Results and Discussion)
5. 结论 (Conclusion)
6. 参考文献 (References)

Format your response with clear section headers. Use academic tone."""

    response = litellm.completion(
        model="claude-3-haiku-20240307",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
    )
    state.final_response = response.choices[0].message.content
    if results:
        state.citations = [f"paper:{c.get('paper_id', '?')}" for c in results[:5]]
    return state


write_report_skill = Skill(
    name="write-report",
    description="生成研究报告，基于知识库内容撰写学术报告",
    trigger_phrases=["写报告", "生成报告", "write report", "报告", "写总结", "总结报告"],
    system_prompt="你是一个学术报告撰写助手。检索知识库并生成结构化的研究报告。",
    handler=_execute_write_report,
)