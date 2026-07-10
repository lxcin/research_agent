# tests/test_cli.py
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from research_agent.cli import main


@patch("research_agent.cli.process_user_input")
@patch("research_agent.cli.AgentState")
def test_cli_startup(mock_state, mock_process, temp_data_dir):
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Research Agent" in result.output


@patch("research_agent.cli.process_user_input")
@patch("research_agent.cli.AgentState")
def test_cli_chat_input(mock_state_cls, mock_process, temp_data_dir):
    from research_agent.models import AgentState as RealAgentState

    mock_state_instance = RealAgentState(
        user_input="What is attention?",
        final_response="Answer to your question.",
        confidence="certain",
        citations=[],
    )
    mock_state_cls.return_value = mock_state_instance
    mock_process.return_value = mock_state_instance

    runner = CliRunner()
    result = runner.invoke(main, ["chat", "What is attention?"])
    assert result.exit_code == 0
    assert "Answer to your question" in result.output