import litellm
from research_agent.models import AgentState
from research_agent.skill import Skill
from research_agent.retrieval import hybrid_search, build_bm25_index


def _execute_literature_review(state: AgentState) -> AgentState:
    query = state.user_input
    for kw in ["写综述", "文献综述", "literature review", "写review", "综述"]:
        query = query.replace(kw, "").strip()
    if not query:
        query = state.active_project.topic if state.active_project else "machine learning"

    build_bm25_index()
    results = hybrid_search(query, n_results=10)
    state.retrieved_chunks = results

    chunks_text = "\n".join([
        f"[{i+1}] {c['text'][:300]}"
        for i, c in enumerate(results[:5])
    ]) if results else "No relevant documents found."

    prompt = f"""You are a research literature review assistant. Write a comprehensive literature review based on the context below.

Topic: {query}

Context:
{chunks_text}

Write a structured literature review in Chinese. Include:
1. 研究背景 (Background)
2. 主要方法 (Main Methods)
3. 关键发现 (Key Findings)
4. 研究空白 (Research Gaps)
5. 参考文献 (References)

Format your response with clear section headers."""

    response = litellm.completion(
        model="claude-3-haiku-20240307",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
    )
    state.final_response = response.choices[0].message.content
    if results:
        state.citations = [f"paper:{c.get('paper_id', '?')}" for c in results[:5]]
    return state


literature_review_skill = Skill(
    name="literature-review",
    description="生成文献综述，汇总知识库中的相关研究",
    trigger_phrases=["写综述", "文献综述", "literature review", "写review", "文献总结", "综述"],
    system_prompt="你是一个文献综述助手。检索知识库并生成结构化的文献综述。",
    handler=_execute_literature_review,
)