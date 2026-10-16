import pytest
from research_agent.execution import execute_once, exclusive_request, CancellationToken, RecoveryRequired
from research_agent.tools.schema import ToolResult


def test_completed_call_replays_without_side_effect():
    calls = []
    def invoke():
        calls.append(1)
        return ToolResult.ok(value=42)
    assert execute_once("run", "call", "write", {}, invoke).success
    assert execute_once("run", "call", "write", {}, invoke).data == {"value": 42}
    assert len(calls) == 1
    with pytest.raises(RecoveryRequired):
        execute_once("run", "call", "write", {"changed": True}, invoke)


def test_uncertain_outcome_is_not_reexecuted():
    calls = []
    def fail():
        calls.append(1)
        raise RuntimeError("side effect may already have happened")
    with pytest.raises(RuntimeError):
        execute_once("run", "call", "write", {}, fail)
    with pytest.raises(RecoveryRequired):
        execute_once("run", "call", "write", {}, fail)
    assert calls == [1]


def test_same_workspace_is_rejected_other_workspace_can_run(tmp_path):
    with exclusive_request(str(tmp_path / "a"), "a"):
        with pytest.raises(RuntimeError):
            with exclusive_request(str(tmp_path / "a"), "b"):
                pass
        with exclusive_request(str(tmp_path / "b"), "b"):
            pass
    with exclusive_request(str(tmp_path / "a"), "a"):
        pass


def test_cancelled_request_does_not_start_model(tmp_path):
    from research_agent.agent import run_agent
    from research_agent.models import AgentState
    token = CancellationToken()
    token.cancel()
    result = run_agent("test", None, AgentState(), workspace_dir=str(tmp_path), cancellation=token)
    assert result.termination_reason == "cancelled"
