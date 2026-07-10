"""End-to-end integration tests."""
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from research_agent.cli import main


def _make_mock_response(content: str):
    return MagicMock(choices=[MagicMock(message=MagicMock(content=content))])


@patch("research_agent.agent.litellm.completion")
def test_full_chat_flow(mock_completion, temp_data_dir):
    from research_agent.store import init_db, insert_paper
    from research_agent.vector_store import add_chunks
    from research_agent.models import Paper

    init_db()
    insert_paper(Paper(
        id="p_int", title="Attention Paper", doi="10.000/int", year=2020,
        source_score=9, citation_count=100, authors=["Author A"],
        abstract="Attention mechanisms in deep learning."
    ))
    add_chunks("p_int", [
        {"chunk_index": 0, "text": "Attention mechanisms are a fundamental component of modern neural networks."},
        {"chunk_index": 1, "text": "Self-attention enables parallel processing of sequential data."},
    ])

    call_count = [0]

    def mock_side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return _make_mock_response("attention mechanism")
        elif call_count[0] == 2:
            return _make_mock_response('{"needs_retrieval": true, "search_query": "attention mechanism"}')
        else:
            return _make_mock_response("Based on the literature:\n\nAttention mechanisms are a key innovation in neural networks. According to the paper, self-attention enables parallel processing.\n\n---\n引用来源: paper:p_int\n自信度: 确定 - supported by retrieved context")

    mock_completion.side_effect = mock_side_effect

    with patch("research_agent.agent.evaluate_retrieval_sufficiency", return_value=True):
        runner = CliRunner()
        result = runner.invoke(main, ["chat", "what is attention mechanism?"])
        assert result.exit_code == 0
        assert "Attention" in result.output or "attention" in result.output.lower()


@patch("research_agent.agent.litellm.completion")
def test_multi_project_routing(mock_completion, temp_data_dir):
    from research_agent.store import init_db, insert_project
    from research_agent.models import Project, ProjectStatus

    init_db()
    insert_project(Project(id="chem_proj", topic="HPLC compound screening", status=ProjectStatus.ACTIVE,
                           history_summary="We analyzed compound purity by HPLC."))
    insert_project(Project(id="bio_proj", topic="protein expression in E. coli", status=ProjectStatus.ACTIVE,
                           history_summary="Checking protein expression levels."))

    call_count = [0]

    def mock_side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return _make_mock_response('{"needs_retrieval": true, "search_query": "HPLC purity analysis"}')
        else:
            return _make_mock_response("HPLC analysis结果通常包括...\n---\n引用来源: [knowledge base]\n自信度: 推测")

    mock_completion.side_effect = mock_side_effect

    with patch("research_agent.agent.evaluate_retrieval_sufficiency", return_value=True):
        runner = CliRunner()
        result = runner.invoke(main, ["chat", "上次那个HPLC结果怎么样"])
        assert result.exit_code == 0