"""Register all built-in tools with the global registry.

Feature-gated tools (Tier B memory: memorize / search_memory) are reconciled on
every register_builtins() call so a runtime feature disable takes effect on the
next session: tools of a disabled feature are unregistered, tools of an enabled
feature are (re)registered.
"""
from research_agent.tools import get_registry
from research_agent.tools.builtin.retrieve import retrieve_tool, search_tool, read_paper_tool, update_notes_tool, delete_paper_tool
from research_agent.tools.builtin.filesystem import shell_exec_tool, file_read_tool, file_write_tool, file_glob_tool, file_grep_tool, file_edit_tool, check_tasks_tool
from research_agent.tools.subagent import spawn_subagent_tool

# Tool names owned by optional features, so register_builtins can keep the
# singleton registry in sync with current feature enable/disable state.
_FEATURE_TOOLS = {
    "memory_tier_b": ("memorize", "search_memory"),
}


def _sync_feature_tools(registry):
    """Register/unregister feature-owned tools per current enabled state.

    Must run every time register_builtins() is called so a feature disabled
    mid-process stops exposing its tools on the next agent run.
    """
    from research_agent.features import is_enabled
    for feature_id, tool_names in _FEATURE_TOOLS.items():
        enabled = is_enabled(feature_id)
        for name in tool_names:
            if enabled:
                if name in registry:
                    continue
                # (re)register from the source module
                from research_agent.tools.builtin.memory_tool import (
                    memorize_tool, search_memory_tool)
                _tool = {"memorize": memorize_tool,
                         "search_memory": search_memory_tool}[name]
                registry.register(_tool)
            else:
                registry.unregister(name)


def register_builtins():
    registry = get_registry()
    # Research layer
    registry.register(retrieve_tool)
    registry.register(search_tool)
    registry.register(read_paper_tool)
    registry.register(update_notes_tool)
    registry.register(delete_paper_tool)
    # Execution layer
    registry.register(shell_exec_tool)
    # File system layer
    registry.register(file_read_tool)
    registry.register(file_write_tool)
    registry.register(file_glob_tool)
    registry.register(file_grep_tool)
    registry.register(file_edit_tool)
    registry.register(check_tasks_tool)
    # Subagent spawn
    registry.register(spawn_subagent_tool)
    # Tier B long-term memory tools — reconciled with feature state each call.
    _sync_feature_tools(registry)
