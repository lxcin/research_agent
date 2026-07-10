import json
from research_agent.models import AgentState, Paper
from research_agent.skill import Skill
from research_agent.search import search_papers
from research_agent.store import init_db, insert_paper


def _execute_paper_search(state: AgentState) -> AgentState:
    query = state.user_input
    for kw in ["搜索论文", "搜论文", "找论文", "search paper"]:
        query = query.replace(kw, "").strip()
    if not query:
        query = "machine learning"

    results = search_papers(query, limit=10)
    if not results:
        state.final_response = "未找到相关论文。"
        return state

    lines = [f"搜索「{query}」找到 {len(results)} 篇论文:\n"]
    for i, paper in enumerate(results[:5]):
        authors_str = ", ".join(paper.get("authors", [])[:3])
        if len(paper.get("authors", [])) > 3:
            authors_str += " et al."
        lines.append(
            f"{i+1}. {paper['title']} ({paper['year']})\n"
            f"   作者: {authors_str}\n"
            f"   引用: {paper['citation_count']}  |  DOI: {paper['doi']}\n"
            f"   摘要: {paper.get('abstract', 'N/A')[:200]}\n"
        )

    for paper in results[:5]:
        init_db()
        insert_paper(Paper(
            id=paper["paper_id"],
            title=paper["title"],
            doi=paper["doi"],
            year=paper["year"],
            citation_count=paper["citation_count"],
            authors=paper["authors"],
            abstract=paper.get("abstract", ""),
        ))

    state.final_response = "\n".join(lines)
    state.citations = [f"paper:{p['paper_id']}" for p in results[:5]]
    return state


paper_search_skill = Skill(
    name="paper-search",
    description="搜索 Semantic Scholar 论文数据库",
    trigger_phrases=["搜索论文", "搜论文", "找论文", "search paper", "查找文献", "找文献"],
    system_prompt="你是一个论文搜索助手。根据用户查询搜索 Semantic Scholar 并返回结果。",
    handler=_execute_paper_search,
)