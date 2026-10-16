# tests/test_feature_gating.py — runtime enable/disable gating for plugins
import pytest

from research_agent.tools import get_registry
from research_agent.tools.builtin import register_builtins


@pytest.fixture
def clean_registry(temp_data_dir):
    yield get_registry()


def test_register_builtins_registers_telemetry_tools(clean_registry):
    register_builtins()
    assert "usage_report" in clean_registry
    assert "usage_query" in clean_registry


def test_disable_plugin_unregisters_tools(clean_registry):
    """Runtime disable removes a plugin's tools immediately."""
    register_builtins()
    assert "usage_report" in clean_registry
    assert clean_registry.disable_plugin("telemetry") is True
    assert "usage_report" not in clean_registry
    # re-enable path
    assert clean_registry.enable_plugin("telemetry") is True
    assert "usage_report" in clean_registry


def test_uninstall_plugin_persists_disabled(clean_registry):
    register_builtins()
    assert clean_registry.uninstall_plugin("plan", force=True) is True
    assert "plan" not in clean_registry.plugins
    assert "plan_update" not in clean_registry


def test_server_feature_guard_404_when_disabled(monkeypatch):
    from fastapi import HTTPException
    from research_agent import server as srv
    import research_agent.tools as tools_pkg
    monkeypatch.setattr(tools_pkg, "is_plugin_enabled", lambda pid, default=True: False)
    with pytest.raises(HTTPException) as exc:
        srv._feature_guard("web", "联网")
    assert exc.value.status_code == 404


def test_server_feature_guard_pass_when_enabled(monkeypatch):
    from research_agent import server as srv
    import research_agent.tools as tools_pkg
    monkeypatch.setattr(tools_pkg, "is_plugin_enabled", lambda pid, default=True: True)
    srv._feature_guard("web")  # must not raise
