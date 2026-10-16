from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from research_agent.models import AgentState
from research_agent.tools import ToolRegistry
from research_agent.tools.schema import ToolPlugin, ToolSchema, ToolResult


def test_registry_unload_waits_for_actual_tool():
    registry = ToolRegistry()
    entered, release = Event(), Event()
    def handler(*args):
        entered.set()
        assert release.wait(5)
        return ToolResult.ok(value=1)
    tool = ToolSchema("owned", "unique owned tool", {}, handler, plugin_id="owner")
    registry.install_plugin(ToolPlugin("owner", tools=[tool]), apply_enabled=False)
    scope = registry.lifecycle.contexts["owner"].scope
    with ThreadPoolExecutor(max_workers=2) as pool:
        call = pool.submit(registry.dispatch, "owned", {}, None, AgentState(), None)
        assert entered.wait(5)
        stop = pool.submit(registry.uninstall_plugin, "owner")
        with scope._condition:
            assert scope._condition.wait_for(lambda: scope.draining, 5)
        assert not stop.done()
        assert not registry.dispatch("owned", {}, None, AgentState(), None).success
        release.set()
        assert call.result(5).success
        assert stop.result(5)
    assert "owned" not in registry


def test_registry_session_stop_is_local():
    registry = ToolRegistry()
    registry.register(ToolSchema("read", "read test", {}, lambda *a: ToolResult.ok()))
    registry.close_session("a")
    assert not registry.dispatch("read", {}, None, AgentState(active_chat_id="a"), None).success
    assert registry.dispatch("read", {}, None, AgentState(active_chat_id="b"), None).success
