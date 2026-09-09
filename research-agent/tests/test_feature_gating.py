# tests/test_feature_gating.py — runtime enable/disable gating for plugins
import pytest

from research_agent.tools import get_registry
from research_agent.tools.builtin import register_builtins


@pytest.fixture
def clean_registry(temp_data_dir):
    """Registry with memory plugin enabled/disabled cleanly."""
    reg = get_registry()
    # ensure clean enabled state (drop any stale config)
    for name in ("memorize", "search_memory"):
        reg.unregister(name)
    if reg.get_plugin("memory"):
        reg._set_plugin_config("memory", True)
    yield reg


def test_register_builtins_registers_memory_tools_when_enabled(clean_registry):
    register_builtins()
    assert "memorize" in clean_registry
    assert "search_memory" in clean_registry


def test_disable_plugin_unregisters_memory_tools(clean_registry):
    """Runtime disable removes memory tools immediately."""
    register_builtins()
    assert "memorize" in clean_registry
    assert clean_registry.disable_plugin("memory") is True
    assert "memorize" not in clean_registry
    assert "search_memory" not in clean_registry
    # re-enable path
    assert clean_registry.enable_plugin("memory") is True
    assert "memorize" in clean_registry


def test_uninstall_plugin_persists_disabled(clean_registry):
    register_builtins()
    assert clean_registry.uninstall_plugin("memory", force=True) is True
    assert "memory" not in clean_registry.plugins
    assert "memorize" not in clean_registry


def test_server_feature_guard_404_when_disabled(monkeypatch):
    from fastapi import HTTPException
    from research_agent import server as srv
    import research_agent.tools as tools_pkg
    monkeypatch.setattr(tools_pkg, "is_plugin_enabled", lambda pid, default=True: False)
    with pytest.raises(HTTPException) as exc:
        srv._feature_guard("memory", "记忆")
    assert exc.value.status_code == 404


def test_server_feature_guard_pass_when_enabled(monkeypatch):
    from research_agent import server as srv
    import research_agent.tools as tools_pkg
    monkeypatch.setattr(tools_pkg, "is_plugin_enabled", lambda pid, default=True: True)
    srv._feature_guard("memory")  # must not raise


def test_memory_tool_limit_guard():
    """LLM passing a non-numeric limit must not crash search_memory."""
    from research_agent.tools.builtin.memory_tool import _handle_search_memory
    from research_agent.models import AgentState
    from research_agent.memory.tier_b import get_manager
    from research_agent.memory.models import MemoryUnit, MemoryKind
    get_manager().write(MemoryUnit(text="用户偏好用中文", kind=MemoryKind.PREFERENCE))
    state = AgentState(user_input="x")
    result = _handle_search_memory({"query": "用户偏好", "limit": "abc"},
                                   None, state, lambda et, d: None)
    assert result.success
    assert result.data["found"] >= 1
