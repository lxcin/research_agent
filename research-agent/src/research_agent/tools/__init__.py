"""ToolRegistry - centralized tool management with deduplication."""
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

from research_agent.tools.schema import ToolSchema, ToolResult, EventCallback


class ToolRegistry:
    _instance: "ToolRegistry | None" = None

    def __init__(self):
        self._tools: dict[str, ToolSchema] = {}
        self._tool_list_cache: list[dict] | None = None

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    # ── Registration ──

    def register(self, tool: ToolSchema):
        if tool.name in self._tools:
            existing = self._tools[tool.name]
            if existing.category != tool.category:
                raise ValueError(f"Tool '{tool.name}' already registered as '{existing.category}', cannot re-register as '{tool.category}'")
            return
        # Functional dedup check
        for name, existing in self._tools.items():
            if _descriptions_similar(tool.description, existing.description, threshold=0.80):
                raise ValueError(
                    f"Tool '{tool.name}' description is too similar to '{name}'. "
                    f"Please refine the description or remove the duplicate."
                )
        self._tools[tool.name] = tool
        self._tool_list_cache = None

    def unregister(self, name: str):
        if name in self._tools:
            del self._tools[name]
            self._tool_list_cache = None

    # ── User tool loading ──

    def load_from_dir(self, dir_path: str):
        """Scan a directory for .py files and register any ToolSchema instances."""
        import importlib.util
        p = Path(dir_path)
        if not p.is_dir():
            raise FileNotFoundError(f"Directory not found: {dir_path}")
        for f in sorted(p.glob("*.py")):
            if f.name.startswith("_"):
                continue
            spec = importlib.util.spec_from_file_location(f"user_tool_{f.stem}", str(f))
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for attr_name in dir(mod):
                obj = getattr(mod, attr_name)
                if isinstance(obj, ToolSchema):
                    self.register(obj)

    # ── LLM integration ──

    def list_for_llm(self) -> list[dict]:
        """Generate OpenAI-compatible tool list for function calling."""
        if self._tool_list_cache is None:
            self._tool_list_cache = [t.to_openai_schema() for t in self._tools.values()]
        return self._tool_list_cache

    def generate_capabilities(self) -> str:
        """Generate a human-readable capability description from registered tools.
        Injected into the system prompt so the LLM knows what it can do."""
        if not self._tools:
            return "你目前没有任何可用工具。"
        lines = ["可用工具："]
        for tool in self._tools.values():
            lines.append(f"- {tool.name}: {tool.description}")
        return "\n".join(lines)

    # ── Dispatch ──

    def dispatch(self, name: str, params: dict, llm, state, emit: EventCallback) -> ToolResult:
        """Execute a tool by name with given parameters."""
        if name not in self._tools:
            return ToolResult.fail(f"Unknown tool: {name}")
        tool = self._tools[name]
        try:
            return tool.handler(params, llm, state, emit)
        except Exception as e:
            return ToolResult.fail(f"Tool '{name}' failed: {str(e)}")

    # ── Skills ──

    def find_skill(self, user_input: str) -> ToolSchema | None:
        """Find a skill-type tool that matches the user input."""
        for tool in self._tools.values():
            if tool.category == "skill" and tool.triggers:
                for trigger in tool.triggers:
                    if trigger.lower() in user_input.lower():
                        return tool
        return None

    @property
    def tools(self) -> dict[str, ToolSchema]:
        return dict(self._tools)

    @property
    def count(self) -> int:
        return len(self._tools)


def _descriptions_similar(a: str, b: str, threshold: float = 0.80) -> bool:
    """Check if two tool descriptions are too similar."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() > threshold


# Global singleton
def get_registry() -> ToolRegistry:
    if ToolRegistry._instance is None:
        ToolRegistry._instance = ToolRegistry()
    return ToolRegistry._instance