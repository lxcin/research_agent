import pytest
from research_agent.models import AgentState
from research_agent.state_components import StateComponent
from research_agent.tools import ToolRegistry
from research_agent.tools.schema import ToolPlugin


def test_state_fields_are_injected_and_removable():
    state = AgentState(fields=[StateComponent("goal", str)], goal="first")
    assert state.goal == "first"
    assert not hasattr(state, "user_input")
    state.goal = "second"
    assert state.components.get("goal") == "second"
    state.components.remove("goal")
    assert not hasattr(state, "goal")
    with pytest.raises(AttributeError):
        state.goal = "third"


def test_plugin_state_injection_and_lifecycle():
    cleaned = []
    registry = ToolRegistry()
    first, second = AgentState(), AgentState()
    registry.bind_state(first)
    plugin = ToolPlugin(id="example", state_components=[
        StateComponent("example.task", dict, cleaned.append)])
    registry.install_plugin(plugin, apply_enabled=False)
    registry.bind_state(second)
    value = first.components.get("example.task")
    value["step"] = 1
    assert second.components.get("example.task") == {}
    registry.bind_state(first)
    assert first.components.get("example.task") is value
    registry.disable_plugin("example")
    assert "example.task" not in first.components.keys()
    assert len(cleaned) == 2
    registry.enable_plugin("example")
    assert first.components.get("example.task") == {}
    registry.uninstall_plugin("example")
    assert "example.task" not in second.components.keys()


def test_component_cleanup_failure_retains_value():
    state = AgentState()
    def fail(value):
        raise RuntimeError("cleanup failed")
    state.components.inject(StateComponent("custom", dict, fail))
    with pytest.raises(RuntimeError):
        state.components.remove("custom")
    assert state.components.get("custom") == {}
