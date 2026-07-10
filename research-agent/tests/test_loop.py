# tests/test_loop.py
from research_agent.loop import self_check, evaluate_retrieval_sufficiency, boundary_check
from research_agent.models import AgentState


def test_evaluate_retrieval_sufficient():
    state = AgentState(
        user_input="attention mechanism",
        retrieved_chunks=[
            {"text": "Attention mechanisms compute weighted sums.", "score": 0.95},
            {"text": "Self-attention allows parallel processing.", "score": 0.87},
            {"text": "Multi-head attention captures different subspaces.", "score": 0.82},
        ]
    )
    assert evaluate_retrieval_sufficiency(state) is True


def test_evaluate_retrieval_insufficient():
    state = AgentState(
        user_input="quantum entanglement in photosynthesis",
        retrieved_chunks=[
            {"text": "Plants use chlorophyll for photosynthesis.", "score": 0.4},
        ]
    )
    assert evaluate_retrieval_sufficiency(state) is False


def test_evaluate_retrieval_empty():
    state = AgentState(
        user_input="some query",
        retrieved_chunks=[]
    )
    assert evaluate_retrieval_sufficiency(state) is False


def test_self_check_without_error():
    state = AgentState(
        user_input="test query",
        final_response="Based on the paper by Smith et al., attention mechanisms improve NLP tasks.",
        retrieved_chunks=[
            {"text": "Attention mechanisms improve NLP.", "paper_id": "paper_1", "source_score": 8}
        ],
        retry_count=0,
    )
    result = self_check(state)
    assert result.error == ""


def test_self_check_hallucinated_citation():
    state = AgentState(
        user_input="test",
        final_response="According to Johnson 2025, gravity is caused by magnets.",
        retrieved_chunks=[
            {"text": "Gravity is a fundamental force explained by general relativity.", "paper_id": "p1"}
        ],
        retry_count=0,
    )
    result = self_check(state)


def test_boundary_check_experiment():
    result = boundary_check("帮我跑个HPLC实验")
    assert result["can_do"] is False
    assert "需要你" in result["suggestion"]


def test_boundary_check_retrieval():
    result = boundary_check("attention mechanism是什么")
    assert result["can_do"] is True


def test_boundary_check_uncertain():
    result = boundary_check("这个分子的晶体结构在300K下的自由能是多少")
    assert not result["can_do"] or "不确定" in result["suggestion"]