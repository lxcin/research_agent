"""Response validation: detect hallucination, verify citations, mark confidence."""
import re
from research_agent.models import AgentState


def validate_response(state: AgentState) -> AgentState:
    """Code-based validation of LLM response. No LLM calls."""
    response = state.final_response or ""
    chunks = state.retrieved_context or []

    # 1. Extract citations from response text
    cited_ids = _extract_cited_paper_ids(response)

    # 2. Cross-reference: are cited papers in retrieved context?
    valid_ids = set()
    for chunk in chunks:
        pid = chunk.get("paper_id", "")
        if pid:
            valid_ids.add(pid)

    hallucinated = [cid for cid in cited_ids if cid not in valid_ids]

    # 3. If LLM claimed to cite but source doesn't exist → hallucination
    if cited_ids and hallucinated:
        state.error = "hallucinated_citation: " + ", ".join(hallucinated)
        state.confidence = "uncertain"
        return state

    # 4. If LLM retrieved but didn't cite → mark as "未标注来源"
    if chunks and not cited_ids:
        state.confidence = "speculative"
        state.error = "retrieved_but_not_cited"
        return state

    # 5. If LLM gave factual answer without retrieval → mark as "无引用来源"
    if not cited_ids and not chunks:
        state.confidence = "speculative"
        state.error = "no_retrieval_no_citation"
        return state

    # 6. All citations valid
    state.confidence = "certain"
    state.citations = [f"paper:{cid}" for cid in cited_ids]
    return state


def _extract_cited_paper_ids(text: str) -> list[str]:
    """Extract paper IDs from response text citations."""
    ids = []
    # Pattern 1: paper:UUID (hex chars + hyphens)
    for m in re.finditer(r"paper:([a-zA-Z0-9\-]{10,})", text):
        ids.append(m.group(1))
    # Pattern 2: paper_id or paper id
    for m in re.finditer(r"paper[_ ]?id[:\s]*([a-zA-Z0-9\-]{10,})", text, re.IGNORECASE):
        ids.append(m.group(1))
    return list(set(ids))