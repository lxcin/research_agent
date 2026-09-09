# tests/test_tool_plugins.py — ToolPlugin (engineering granularity) install/uninstall
import pytest

from research_agent.tools import get_registry
from research_agent.tools.builtin import register_builtins
from research_agent.tools.schema import ToolPlugin


@pytest.fixture
def fresh_registry():
    """A registry with builtins installed and all plugins enabled (clean config)."""
    reg = get_registry()
    # 1) uninstall whatever is present (writes disabled to config)
    for pid in list(reg.plugins):
        reg.uninstall_plugin(pid, force=True)
    # 2) reset config plugin switches to enabled (undo step 1's writes)
    from research_agent.config import load_config, get_config_path
    cfg = load_config()
    cfg["plugins"] = {pid: {"enabled": True}
                      for pid in ("filesystem", "shell", "subagent", "memory",
                                  "diagnostics", "mcp")}
    with open(get_config_path(), "w", encoding="utf-8") as fh:
        import yaml
        yaml.safe_dump(cfg, fh, allow_unicode=True, sort_keys=False)
    # 3) install fresh
    register_builtins()
    return reg


def test_core_plugins_installed(fresh_registry):
    assert "filesystem" in fresh_registry.plugins
    assert "shell" in fresh_registry.plugins
    assert "memory" in fresh_registry.plugins  # memory_tier_b enabled by default
    assert "subagent" in fresh_registry.plugins


def test_subagent_plugin_registered(fresh_registry):
    names = [t.name for t in fresh_registry.tools_of("subagent")]
    assert "spawn_subagent" in names
    assert fresh_registry.tools["spawn_subagent"].plugin_id == "subagent"


def test_every_tool_has_plugin(fresh_registry):
    orphans = [n for n, t in fresh_registry.tools.items() if not t.plugin_id]
    assert orphans == []


def test_filesystem_plugin_owns_file_tools(fresh_registry):
    names = fresh_registry.tools_of("filesystem").tool_names if hasattr(fresh_registry.tools_of("filesystem"), "tool_names") else [t.name for t in fresh_registry.tools_of("filesystem")]
    for n in ("file_read", "file_write", "file_edit", "file_glob", "file_grep"):
        assert n in names
    # tools carry plugin stamp
    fr = fresh_registry.tools["file_read"]
    assert fr.plugin_id == "filesystem"
    assert fr.group == "file"
    assert fr.side_effect is False


def test_shell_plugin_marks_shell_dangerous(fresh_registry):
    sh = fresh_registry.tools["shell_exec"]
    assert sh.plugin_id == "shell"
    assert sh.requires_approval is True
    assert "shell_exec" in fresh_registry.tools_of("shell").tool_names if False else "shell_exec" in [t.name for t in fresh_registry.tools_of("shell")]


def test_uninstall_plugin_removes_tools(fresh_registry):
    assert "memorize" in fresh_registry
    assert fresh_registry.uninstall_plugin("memory", force=True) is True
    assert "memory" not in fresh_registry.plugins
    assert "memorize" not in fresh_registry
    assert "search_memory" not in fresh_registry
    # other plugins unaffected
    assert "shell_exec" in fresh_registry
    assert "file_write" in fresh_registry


def test_core_plugin_cannot_uninstall(fresh_registry):
    with pytest.raises(ValueError):
        fresh_registry.uninstall_plugin("filesystem")


def test_uninstall_missing_plugin(fresh_registry):
    assert fresh_registry.uninstall_plugin("nope") is False


def test_install_plugin_dependency_missing():
    from research_agent.tools import get_registry
    reg = get_registry()
    p = ToolPlugin(id="dep_plugin", depends=["not_installed"], tools=[])
    with pytest.raises(ValueError):
        reg.install_plugin(p)


def test_plugin_dependency_order():
    from research_agent.tools import get_registry
    reg = get_registry()
    base = ToolPlugin(id="_base_t", tools=[])
    child = ToolPlugin(id="_child_t", depends=["_base_t"], tools=[])
    reg.install_plugin(child) if "_base_t" in reg.plugins else reg.install_plugin(base)
    reg.install_plugin(child)
    # cannot remove base while child depends on it
    with pytest.raises(ValueError):
        reg.uninstall_plugin("_base_t")
    reg.uninstall_plugin("_child_t")
    assert reg.uninstall_plugin("_base_t") is True


def test_plugin_id_mismatch_rejected():
    from research_agent.tools import get_registry
    from research_agent.tools.schema import ToolSchema, ToolResult
    reg = get_registry()
    tool = ToolSchema(name="_mismatch", description="x", parameters={},
                      handler=lambda *a, **k: ToolResult.ok(), plugin_id="other")
    p = ToolPlugin(id="mismatch_owner", tools=[tool])
    with pytest.raises(ValueError):
        reg.install_plugin(p)


def test_cli_plugin_list_smoke(temp_data_dir, monkeypatch):
    from click.testing import CliRunner
    from research_agent import cli as cli_mod
    runner = CliRunner()
    res = runner.invoke(cli_mod.plugin, ["list"])
    assert res.exit_code == 0
    assert "filesystem" in res.output
    assert "memory" in res.output
    assert "shell" in res.output


def test_cli_plugin_disable_then_list(temp_data_dir, monkeypatch):
    from click.testing import CliRunner
    from research_agent import cli as cli_mod
    from research_agent.tools import get_registry, is_plugin_enabled
    runner = CliRunner()
    res = runner.invoke(cli_mod.plugin, ["disable", "memory"])
    assert res.exit_code == 0
    assert is_plugin_enabled("memory") is False
    reg = get_registry()
    # memory plugin disabled persists to config; re-enable via CLI
    res2 = runner.invoke(cli_mod.plugin, ["enable", "memory"])
    assert res2.exit_code == 0
    assert is_plugin_enabled("memory") is True


def test_cli_plugin_uninstall_core_rejected(temp_data_dir, monkeypatch):
    from click.testing import CliRunner
    from research_agent import cli as cli_mod
    runner = CliRunner()
    res = runner.invoke(cli_mod.plugin, ["uninstall", "filesystem", "--yes"])
    assert res.exit_code == 0
    assert "core" in res.output
