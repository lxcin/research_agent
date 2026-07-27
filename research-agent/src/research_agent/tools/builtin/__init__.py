"""Register all built-in tools with the global registry."""
from research_agent.tools import get_registry
from research_agent.tools.builtin.retrieve import retrieve_tool, search_tool, read_paper_tool, update_notes_tool
from research_agent.tools.builtin.filesystem import shell_exec_tool, file_read_tool, file_write_tool, file_glob_tool, file_grep_tool, file_edit_tool, check_tasks_tool
from research_agent.tools.git_tool import git_init_tool, git_checkpoint_tool, git_log_tool, git_rollback_tool, git_status_tool
from research_agent.tools.subagent import spawn_subagent_tool


def register_builtins():
    registry = get_registry()
    # Research layer
    registry.register(retrieve_tool)
    registry.register(search_tool)
    registry.register(read_paper_tool)
    registry.register(update_notes_tool)
    # Execution layer
    registry.register(shell_exec_tool)
    # File system layer
    registry.register(file_read_tool)
    registry.register(file_write_tool)
    registry.register(file_glob_tool)
    registry.register(file_grep_tool)
    registry.register(file_edit_tool)
    registry.register(check_tasks_tool)
    # Git integration
    registry.register(git_init_tool)
    registry.register(git_checkpoint_tool)
    registry.register(git_log_tool)
    registry.register(git_rollback_tool)
    registry.register(git_status_tool)
    # Subagent spawn
    registry.register(spawn_subagent_tool)