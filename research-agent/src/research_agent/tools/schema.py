"""Tool schema definitions for the tool registry."""
from dataclasses import dataclass, field
from typing import Callable, Any

EventCallback = Callable[[str, dict], None]


@dataclass
class ToolSchema:
    """Defines a tool that the agent can call."""
    name: str
    description: str
    parameters: dict  # JSON Schema for parameters
    handler: Callable  # async def (params, llm, state, emit) -> ToolResult
    category: str = "general"  # builtin, skill, user, mcp
    triggers: list[str] = field(default_factory=list)  # for skill-type tools

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