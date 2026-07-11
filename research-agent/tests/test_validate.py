"""Tests for response validation."""
from research_agent.validate import validate_response, _extract_cited_paper_ids
from research_agent.models import AgentState


def test_extract_cited_paper_ids():
    text = "根据论文 [1] paper:abc123-def456-ghi789-jkl012 的结论..."
    ids = _extract_cited_paper_ids(text)
    assert "abc123-def456-ghi789-jkl012" in ids


def test_extract_no_citations():
    text = "这是一个普通的回答，没有引用任何论文。"
    ids = _extract_cited_paper_ids(text)
    assert len(ids) == 0


def test_validate_hallucinated_citation():
    state = AgentState(
        user_input="test",
        final_response="根据 paper:fake-id-12345 的研究表明...",
        retrieved_context=[
            {"paper_id": "real-paper-id-67890", "text": "actual content"}
        ]
    )
    result = validate_response(state)
    assert result.error is not None
    assert "hallucinated" in result.error


def test_validate_valid_citation():
    state = AgentState(
        user_input="test",
        final_response="根据 paper:real-paper-id-67890 的研究...",
        retrieved_context=[
            {"paper_id": "real-paper-id-67890", "text": "actual content"}
        ]
    )
    result = validate_response(state)
    assert result.confidence == "certain"
    assert "real-paper-id-67890" in str(result.citations)


def test_validate_no_retrieval_no_citation():
    state = AgentState(
        user_input="test",
        final_response="这是一个普通的回答。",
        retrieved_context=[]
    )
    result = validate_response(state)
    assert result.confidence == "speculative"
    assert result.error == "no_retrieval_no_citation"


def test_validate_retrieved_but_not_cited():
    state = AgentState(
        user_input="test",
        final_response="根据研究，这个结论是正确的。",
        retrieved_context=[
            {"paper_id": "real-paper-id-67890", "text": "actual content"}
        ]
    )
    result = validate_response(state)
    assert result.confidence == "speculative"
    assert result.error == "retrieved_but_not_cited"