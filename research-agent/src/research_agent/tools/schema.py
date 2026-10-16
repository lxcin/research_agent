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
from research_agent.state_components import StateComponent
from research_agent.evaluation import EvaluationSpec
from research_agent.task_evaluation import TaskCase

EventCallback = Callable[[str, dict], None]


class ToolNetworkError(Exception):
    """A plugin may confirm its operation failed without an unknown effect."""
    def __init__(self, message, *, confirmed_failed=False):
        if not isinstance(confirmed_failed, bool):
            raise TypeError("confirmed_failed must be a boolean")
        super().__init__(message)
        self.confirmed_failed = confirmed_failed


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
    evaluations: list[EvaluationSpec] = field(default_factory=list)
    usage: str = ""
    version: str = "1"
    evaluation_cases: list[TaskCase] = field(default_factory=list)
    context_call: Callable[[dict], dict] | None = None
    context_result: Callable[[dict], dict] | None = None
    history_result: Callable[[dict], dict] | None = None
    defer_context: bool = False  # Batch completed history only at host boundaries/watermarks.
    context_probe: Callable | None = None  # (state) -> notice text; never restores source.
    context_delivery: Callable | None = None  # (state, raw_result, visible_result)

    def __post_init__(self):
        if not self.name or not isinstance(self.parameters, dict) or not callable(self.handler):
            raise ValueError("Tool requires a name, object schema and callable handler")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("Tool contract version must be a nonempty string")
        if any(value is not None and not callable(value) for value in (self.context_call, self.context_result, self.history_result, self.context_probe, self.context_delivery)):
            raise TypeError("Tool context projections must be callable")

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description + ("\n使用约定：" + self.usage if self.usage else ""),
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

    def __post_init__(self):
        if not isinstance(self.success, bool) or not isinstance(self.data, dict):
            raise TypeError("ToolResult requires boolean success and object data")
        if not isinstance(self.response, str) or not isinstance(self.chunks, list) or not isinstance(self.events, list):
            raise TypeError("Invalid ToolResult response/chunks/events")

    @staticmethod
    def ok(**data) -> "ToolResult":
        return ToolResult(success=True, data=data)

    @staticmethod
    def fail(reason: str) -> "ToolResult":
        return ToolResult(success=False, data={"error": reason})

    @staticmethod
    def network_error(reason: str) -> "ToolResult":
        return ToolResult(success=False, data={"error": "网络中断: " + reason,
            "error_code": "network_interrupted", "outcome": "failed", "retryable": True})


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
    state_components: list[StateComponent] = field(default_factory=list)
    evaluations: list[EvaluationSpec] = field(default_factory=list)
    # Lifecycle hooks.
    on_install: Callable | None = None
    on_uninstall: Callable | None = None
    on_enable: Callable | None = None
    on_disable: Callable | None = None
    evaluation_cases: list[TaskCase] = field(default_factory=list)

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]
