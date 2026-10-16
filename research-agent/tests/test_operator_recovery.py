import json
import pytest
from research_agent.execution import checkpoint, execute_once, RecoveryRequired, exclusive_request
from research_agent.recovery import inspect_execution, reconcile
from research_agent.tools.schema import ToolResult


def prepare(workspace):
    checkpoint("run", json.dumps(["task", workspace, "chat", "function_calling"]), {"messages": [], "pending": []})
    def uncertain():
        raise RuntimeError("connection lost")
    with pytest.raises(RuntimeError):
        execute_once("run", "c1", "write", {}, uncertain)


def test_verified_completion_resumes_without_repeating_side_effect(tmp_path):
    prepare(str(tmp_path))
    info = inspect_execution("run")
    assert info["calls"][0]["status"] == "started"
    reconcile("run", "c1", decision="completed", note="Operator checked target record", result=ToolResult.ok(id=42))
    result = execute_once("run", "c1", "write", {}, lambda: pytest.fail("duplicate"))
    assert result.data == {"id": 42}


def test_confirmed_not_executed_allows_retry_and_records_audit(tmp_path):
    prepare(str(tmp_path))
    reconcile("run", "c1", decision="not_executed", note="Target record absent; request never accepted")
    assert execute_once("run", "c1", "write", {}, lambda: ToolResult.ok()).success
    from research_agent.recovery import database
    with database() as db:
        assert db.execute("SELECT decision,note FROM reconciliations").fetchone() == (
            "not_executed", "Target record absent; request never accepted")


def test_cannot_reconcile_active_execution_or_without_evidence(tmp_path):
    prepare(str(tmp_path))
    with pytest.raises(ValueError):
        reconcile("run", "c1", decision="not_executed", note="")
    with exclusive_request(str(tmp_path), "chat"):
        with pytest.raises(RuntimeError):
            reconcile("run", "c1", decision="not_executed", note="checked")


def test_cli_inspect_exposes_status_without_tool_arguments(tmp_path):
    prepare(str(tmp_path))
    from click.testing import CliRunner
    from research_agent.cli import main
    result = CliRunner().invoke(main, ["execution", "inspect", "run"])
    assert result.exit_code == 0
    assert json.loads(result.output)["calls"][0] == {"call_id": "c1", "tool": "write", "status": "started"}
