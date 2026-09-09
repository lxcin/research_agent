"""Tool & plugin schema definitions for the tool registry.

Two-level model:
  - ToolSchema  = what the AGENT sees (one callable action; file_read/file_write/...
                  are separate tools even though they share an implementation).
  - ToolPlugin  = what ENGINEERING installs/uninstalls (a cohesive domain sharing
                  implementation/resources). A plugin exposes several tools.

Plugin ≠ tool: tools are the agent-facing granularity, plugins are the
lifecycle/uninstall granularity. Uninstalling a plugin removes all its tools.
"""
from dataclasses import dataclass, field
from typing import Callable

EventCallback = Callable[[str, dict], None]


@dataclass
class ToolSchema:
    """Defines one tool the agent can call (agent-facing granularity)."""
    name: str
    description: str
    parameters: dict  # JSON Schema for parameters
    handler: Callable  # (params, llm, state, emit) -> ToolResult
    category: str = "general"  # builtin, skill, user, mcp
    triggers: list[str] = field(default_factory=list)  # for skill-type tools

    # Plugin membership (engineering grouping). Default "" = standalone / host-level.
    plugin_id: str = ""
    group: str = ""                 # sub-group inside the plugin (file/shell/task...)
    requires_approval: bool = False  # dangerous → host pre_tool_hook consults this
    side_effect: bool = True        # True = may change the outside world

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }


@dataclass
class ToolResult:
    """Result returned by a tool handler."""
    success: bool
    data: dict = field(default_factory=dict)  # returned to LLM as tool result
    chunks: list[dict] = field(default_factory=list)  # retrieved text chunks
    response: str = ""  # direct response text (for generate tool)
    events: list[dict] = field(default_factory=list)  # pre-emitted events

    @staticmethod
    def ok(**data) -> "ToolResult":
        return ToolResult(success=True, data=data)

    @staticmethod
    def fail(reason: str) -> "ToolResult":
        return ToolResult(success=False, data={"error": reason})


@dataclass
class ToolPlugin:
    """A cohesive capability unit (engineering granularity): install/uninstall/
    enable/disable atomically. May own tools, and/or carry behavior hooks
    (e.g. diagnostics has no tools but is still a pluggable capability).

    A plugin declares what it occupies on disk (owned files/packages/tests/data
    dirs) so uninstall can remove everything; and lifecycle hooks so enable /
    disable / uninstall can release resources.
    """
    id: str
    label: str = ""
    depends: list[str] = field(default_factory=list)   # other plugin ids
    tools: list[ToolSchema] = field(default_factory=list)
    # default enabled state (config `plugins.<id>.enabled` can override)
    enabled: bool = True
    is_core: bool = False     # core plugins cannot be uninstalled
    # Disk footprint — declared at install time, used at uninstall.
    owned_files: list[str] = field(default_factory=list)
    owned_packages: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    data_dirs: list[str] = field(default_factory=list)
    # Lifecycle hooks.
    on_install: Callable | None = None
    on_uninstall: Callable | None = None
    on_enable: Callable | None = None
    on_disable: Callable | None = None

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]
