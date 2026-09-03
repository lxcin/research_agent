"""memorize tool — explicit Tier B memory write (user/LLM asked to remember)."""
from research_agent.tools.schema import ToolSchema, ToolResult
from research_agent.memory.models import MemoryUnit, MemoryKind, MemoryScope


def _handle_memorize(params: dict, llm, state, emit) -> ToolResult:
    text = (params.get("text") or "").strip()
    if not text:
        return ToolResult.fail("Missing text")
    kind_raw = (params.get("kind") or "fact").strip().lower()
    if kind_raw not in {k.value for k in MemoryKind}:
        return ToolResult.fail(f"Invalid kind: {kind_raw}")

    try:
        importance = max(0.0, min(1.0, float(params.get("importance", 0.7))))
    except (TypeError, ValueError):
        importance = 0.7

    from research_agent.memory import get_manager
    src = {}
    if state.active_project and getattr(state.active_project, "id", None):
        src["project_id"] = state.active_project.id
    if getattr(state, "workspace_dir", ""):
        src["workspace_dir"] = state.workspace_dir

    unit = MemoryUnit(
        text=text,
        kind=MemoryKind(kind_raw),
        importance=importance,
        scope=MemoryScope.USER,
        source=src,
    )
    saved = get_manager().write(unit)
    emit("tool", {"tool": "memorize", "status": "saved", "id": saved.id})
    return ToolResult.ok(id=saved.id, kind=saved.kind.value,
                         message="已记住", text=text)


memorize_tool = ToolSchema(
    name="memorize",
    description=(
        "把用户明确要求记住的信息写入长期记忆（工具调用结果不进记忆）。"
        "当用户说'记住…''以后都…''我偏好…'等时调用；只传陈述性内容本身。"
        "kind: fact/preference/decision/task/dead_end/insight/reference/style。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要记住的陈述（写成'用户…'，不含工具细节）"},
            "kind": {"type": "string",
                     "description": "类型：fact/preference/decision/task/dead_end/insight/reference/style，默认 fact"},
            "importance": {"type": "number", "description": "0~1 重要性，默认 0.7"},
        },
        "required": ["text"],
    },
    handler=_handle_memorize,
    category="memory",
)
