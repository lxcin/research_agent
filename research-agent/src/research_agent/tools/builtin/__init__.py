"""Built-in capability plugins.

Plugins are the unified capability unit: tool-bearing (filesystem/shell/
subagent/memory) and behavior-only (diagnostics, which hooks the event stream).
Each plugin declares its disk footprint so uninstall can remove files/data; each
is independently enable/disable-able at runtime via config `plugins.<id>`.

Core plugins (filesystem/shell/subagent) are installed by default and cannot be
uninstalled (is_core). register_builtins() reconciles every plugin's tools against
its current enabled state on each call.
"""
from dataclasses import replace

from research_agent.tools import get_registry
from research_agent.tools.schema import ToolPlugin, ToolSchema
from research_agent.tools.builtin.filesystem import (
    file_read_tool, file_write_tool, file_edit_tool, file_glob_tool, file_grep_tool,
    shell_exec_tool, check_tasks_tool,
)
from research_agent.tools.subagent import spawn_subagent_tool


def _stamp(tool: ToolSchema, plugin_id: str, group: str = "",
           requires_approval: bool = False, side_effect: bool = True) -> ToolSchema:
    return replace(tool, plugin_id=plugin_id, group=group,
                   requires_approval=requires_approval, side_effect=side_effect)


def _plugins() -> list[ToolPlugin]:
    mem_dir = "research_agent/memory"
    return [
        # Pure workspace file operations — the "file processing" plugin.
        ToolPlugin(
            id="filesystem",
            label="工作区文件操作（读写/编辑/查找/搜索）",
            is_core=True,
            tools=[
                _stamp(file_read_tool, "filesystem", "file", side_effect=False),
                _stamp(file_write_tool, "filesystem", "file"),
                _stamp(file_edit_tool, "filesystem", "file"),
                _stamp(file_glob_tool, "filesystem", "file", side_effect=False),
                _stamp(file_grep_tool, "filesystem", "file", side_effect=False),
            ],
        ),
        # Command execution — core, dangerous by default.
        ToolPlugin(
            id="shell",
            label="Shell 命令执行与后台任务",
            is_core=True,
            tools=[
                _stamp(shell_exec_tool, "shell", "exec", requires_approval=True),
                _stamp(check_tasks_tool, "shell", "task", side_effect=False),
            ],
        ),
        # Parallel sub-agent orchestration.
        ToolPlugin(
            id="subagent",
            label="并行子代理编排（spawn_subagent）",
            is_core=True,
            tools=[
                _stamp(spawn_subagent_tool, "subagent", "orchestrate", side_effect=False),
            ],
        ),
        # Tier B personal memory — optional, owns memory/ submodules + data dir.
        ToolPlugin(
            id="memory",
            label="个人长期记忆（memorize / search_memory / 提炼）",
            enabled=True,
            tools=[],  # filled in _sync_memory_plugin
            owned_files=[f"{mem_dir}/__init__.py"],
            owned_packages=[f"{mem_dir}.models", f"{mem_dir}.storage",
                            f"{mem_dir}.vector", f"{mem_dir}.source",
                            f"{mem_dir}.extractor", f"{mem_dir}.pipeline",
                            f"{mem_dir}.retrieve", f"{mem_dir}.tier_b"],
            test_files=["tests/test_memory_units.py", "tests/test_memory_write_path.py",
                        "tests/test_memory_read_path.py", "tests/test_polish.py"],
            data_dirs=["memory"],
        ),
        # Diagnostics — behavior-only capability (no tools): event stream, monitor,
        # scan/report/feedback hooks. owns diagnostics/ package + logs data.
        ToolPlugin(
            id="diagnostics",
            label="故障检测与开发者报告（事件流/监控/report）",
            depends=["memory"],  # feedback writes dead_end into Tier B
            enabled=True,
            owned_packages=["research_agent.diagnostics"],
            test_files=["tests/test_diagnostics.py", "tests/test_diagnostics_phase_e.py"],
            data_dirs=["logs", "diagnostics"],
        ),
        # MCP external tools — dynamic; the plugin is a placeholder whose tools are
        # added at runtime by mcp_loader (each server tools tagged plugin_id="mcp").
        ToolPlugin(
            id="mcp",
            label="MCP 外部工具（动态加载）",
            enabled=True,
            owned_files=["research_agent/tools/mcp_loader.py"],
        ),
    ]


def register_builtins():
    """Install all capability plugins, reconciling tools with enabled state."""
    registry = get_registry()
    for plugin in _plugins():
        registry.install_plugin(plugin, apply_enabled=True)
        if not plugin.enabled:
            # install_plugin registers tools only when enabled; ensure disabled
            # plugins have none exposed
            for t in plugin.tools:
                registry.unregister(t.name)
    _sync_memory_plugin_tools(registry)


def _memory_tools():
    from research_agent.tools.builtin.memory_tool import memorize_tool, search_memory_tool
    return [
        _stamp(memorize_tool, "memory", "memory"),
        _stamp(search_memory_tool, "memory", "memory", side_effect=False),
    ]


def _sync_memory_plugin_tools(registry):
    """Keep memory plugin's tools in sync with its enabled state."""
    plugin = registry.get_plugin("memory")
    if plugin is None:
        return
    # reconcile tools to whatever the registry currently exposes for 'memory'
    if plugin.enabled:
        if not registry.get_plugin("memory").tools:
            registry.get_plugin("memory").tools = _memory_tools()
        for t in _memory_tools():
            if t.name not in registry:
                registry.register(t)
    else:
        for t in _memory_tools():
            registry.unregister(t.name)
