import pytest
from concurrent.futures import ThreadPoolExecutor
from research_agent.tools import ToolRegistry
from research_agent.tools.schema import ToolSchema, ToolResult
from research_agent.models import AgentState
from research_agent.state_components import StateComponent, ComponentStore


def test_schema_rejects_bad_input_before_handler_and_checks_nested_values():
    calls = []
    registry = ToolRegistry()
    registry.register(ToolSchema("custom", "custom contract", {
        "type": "object", "properties": {"items": {"type": "array", "minItems": 1,
        "items": {"type": "integer", "minimum": 1}}}, "required": ["items"], "additionalProperties": False},
        lambda *a: calls.append(1) or ToolResult.ok()))
    for params in ({}, {"items": []}, {"items": [True]}, {"items": [-1]}, {"items": [1], "extra": 2}):
        assert not registry.dispatch("custom", params, None, AgentState(), None).success
    assert calls == []
    assert registry.dispatch("custom", {"items": [1]}, None, AgentState(), None).success


def test_remote_schema_reference_is_rejected_at_registration():
    registry = ToolRegistry()
    with pytest.raises(ValueError):
        registry.register(ToolSchema("remote", "remote schema", {"$ref": "https://example.com/schema"}, lambda *a: ToolResult.ok()))


def test_owned_view_is_detached_typed_and_updates_atomically():
    store = ComponentStore()
    store.inject(StateComponent("plugin.count", lambda: {"count": 0}, owner="plugin", value_type=dict))
    view = store.view("plugin")
    detached = view.get("plugin.count")
    detached["count"] = 99
    assert view.get("plugin.count")["count"] == 0
    with pytest.raises(PermissionError):
        store.view("other").set("plugin.count", {})
    with pytest.raises(TypeError):
        view.set("plugin.count", [])
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: view.update("plugin.count", lambda value: {"count": value["count"] + 1}), range(100)))
    assert view.get("plugin.count") == {"count": 100}


def test_restore_rejects_changed_owner_without_partial_update(tmp_path):
    original = ComponentStore()
    original.inject(StateComponent("data", dict, owner="one", value_type=dict, encode=dict, decode=dict))
    path = tmp_path / "state.json"
    original.save(path)
    other = ComponentStore()
    other.inject(StateComponent("data", dict, owner="two", value_type=dict, encode=dict, decode=dict))
    with pytest.raises(ValueError, match="owner"):
        other.restore(path)
    assert other.get("data") == {}
