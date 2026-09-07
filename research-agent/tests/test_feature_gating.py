# tests/test_feature_gating.py — runtime gating for optional features
import pytest

from research_agent import features as feats
from research_agent.tools import get_registry
from research_agent.tools.builtin import register_builtins


@pytest.fixture
def clean_registry(temp_data_dir):
    """Isolated registry so tool sync tests don't pollute other suites."""
    reg = get_registry()
    for name in ("memorize", "search_memory"):
        reg.unregister(name)
    yield reg
    for name in ("memorize", "search_memory"):
        reg.unregister(name)


def test_register_builtins_registers_memory_tools_when_enabled(clean_registry):
    register_builtins()
    assert "memorize" in clean_registry
    assert "search_memory" in clean_registry


def test_register_builtins_unregisters_when_disabled(clean_registry, monkeypatch):
    """Feature disable must remove its tools on next register_builtins()."""
    import research_agent.features as feats_pkg
    monkeypatch.setattr(feats_pkg, "is_enabled", lambda fid: fid != "memory_tier_b")
    register_builtins()
    assert "memorize" not in clean_registry
    assert "search_memory" not in clean_registry
    # re-enable path
    monkeypatch.setattr(feats_pkg, "is_enabled", lambda fid: True)
    register_builtins()
    assert "memorize" in clean_registry


def test_server_feature_guard_404_when_disabled(monkeypatch):
    from fastapi import HTTPException
    from research_agent import server as srv
    import research_agent.features as feats_pkg
    monkeypatch.setattr(feats_pkg, "is_enabled", lambda fid: False)
    with pytest.raises(HTTPException) as exc:
        srv._feature_guard("knowledge_graph", "论文库")
    assert exc.value.status_code == 404


def test_server_feature_guard_pass_when_enabled(monkeypatch):
    from research_agent import server as srv
    import research_agent.features as feats_pkg
    monkeypatch.setattr(feats_pkg, "is_enabled", lambda fid: True)
    srv._feature_guard("knowledge_graph")  # must not raise


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
