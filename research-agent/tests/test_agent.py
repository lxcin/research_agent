# tests/test_agent.py
from unittest.mock import patch, MagicMock
from research_agent.agent import (
    build_graph, process_user_input, router_node, reasoner_node,
    retriever_node, generator_node, AgentState,
)
from research_agent.models import Project, ProjectStatus


@patch("research_agent.agent.litellm.completion")
def test_router_node_routes_to_existing_project(mock_completion, temp_data_dir):
    from research_agent.store import init_db, insert_project

    init_db()
    insert_project(Project(id="p1", topic="HPLC compound screening", status=ProjectStatus.ACTIVE,
                           history_summary="We discussed HPLC purity analysis."))

    state = AgentState(user_input="上次那个 HPLC 结果拿到了")
    result = router_node(state)
    assert result.active_project is not None
    assert result.active_project.id == "p1"


@patch("research_agent.agent.litellm.completion")
def test_router_node_creates_new_project(mock_completion, temp_data_dir):
    from research_agent.store import init_db

    init_db()
    mock_completion.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="MOF gas adsorption"))]
    )

    state = AgentState(user_input="我想开一个新项目，研究MOF材料的气体吸附性能")
    result = router_node(state)
    assert result.active_project is not None
    assert "MOF" in result.active_project.topic or "adsorption" in result.active_project.topic.lower()


@patch("research_agent.agent.litellm.completion")
def test_reasoner_node_determines_retrieval_needed(mock_completion, temp_data_dir):
    mock_completion.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content='{"needs_retrieval": true, "search_query": "attention mechanism in transformers"}'))]
    )
    state = AgentState(user_input="attention mechanism是什么？")
    result = reasoner_node(state)
    assert result.retry_count == 0


@patch("research_agent.agent.litellm.completion")
def test_retriever_node_finds_chunks(mock_completion, temp_data_dir):
    from research_agent.vector_store import add_chunks

    add_chunks("test_p", [
        {"chunk_index": 0, "text": "Attention mechanisms compute weighted sums of values."},
    ])

    state = AgentState(user_input="attention", active_project=Project(topic="test"))
    result = retriever_node(state)
    assert len(result.retrieved_chunks) > 0


@patch("research_agent.agent.litellm.completion")
def test_generator_node_produces_response(mock_completion, temp_data_dir):
    mock_completion.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Attention is a mechanism that..."))]
    )
    state = AgentState(
        user_input="什么是attention?",
        retrieved_chunks=[{"text": "Attention computes weighted sums.", "paper_id": "p1", "source_score": 10}],
    )
    result = generator_node(state)
    assert len(result.final_response) > 0
    assert result.confidence != ""