import asyncio
from fastapi import HTTPException
import pytest
from research_agent.models import AgentState
from research_agent.execution import CancellationToken, execute_once
from research_agent.server import cancel_execution, _active_states, _active_lock
from research_agent.tools.schema import ToolResult


def test_cancel_api_targets_execution_and_wakes_approval():
    from threading import Event
    first, other = AgentState(), AgentState()
    first._cancellation, other._cancellation = CancellationToken(), CancellationToken()
    event = Event()
    first._pending_confirms["approval"] = {"event": event, "approved": False}
    with _active_lock:
        _active_states.update({"first": first, "other": other})
    try:
        assert asyncio.run(cancel_execution("first"))["status"] == "cancellation_requested"
        assert event.is_set()
        other._cancellation.check()
    finally:
        with _active_lock:
            _active_states.pop("first")
            _active_states.pop("other")
    with pytest.raises(HTTPException):
        asyncio.run(cancel_execution("first"))


def test_live_resource_does_not_break_result_journal_or_get_overwritten():
    state = AgentState()
    resource = object()
    state.caps.update({"sandbox": resource, "plan": {"step": 1}})
    result = execute_once("run", "call", "tool", {}, lambda: ToolResult.ok(), state=state)
    assert result.success
    state.caps["plan"] = {}
    execute_once("run", "call", "tool", {}, lambda: pytest.fail("must not repeat"), state=state)
    assert state.caps["sandbox"] is resource
    assert state.caps["plan"] == {"step": 1}
