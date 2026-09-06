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

    from research_agent.memory.tier_b import get_manager
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


def _parse_scope(raw: str):
    from research_agent.memory.models import MemoryScope
    if raw in {s.value for s in MemoryScope}:
        return MemoryScope(raw)
    return MemoryScope.USER


def _parse_kind(raw: str):
    from research_agent.memory.models import MemoryKind
    if raw in {k.value for k in MemoryKind}:
        return MemoryKind(raw)
    return None


def _handle_search_memory(params: dict, llm, state, emit) -> ToolResult:
    """Retrieve user's personal memory by keyword query (LLM-invoked agentic read)."""
    query = (params.get("query") or "").strip()
    if not query:
        return ToolResult.fail("Missing query")

    from research_agent.memory.tier_b import get_manager
    scope = _parse_scope(params.get("scope", "user"))
    kind = _parse_kind(params.get("kind", ""))
    limit = max(1, min(int(params.get("limit", 5)), 20))

    hits = get_manager().retrieve(query, scope=scope, kind=kind, limit=limit)
    if not hits:
        emit("tool", {"tool": "search_memory", "status": "empty", "query": query})
        return ToolResult.ok(found=0, hits=[],
                             message="记忆库无匹配（可能从未记住相关内容）")

    # readable block for the LLM, plus structured hits
    from research_agent.memory.retrieve import format_hits
    formatted = format_hits(hits)
    structured = [
        {"text": u.text, "kind": u.kind.value, "importance": u.importance,
         "scope": u.scope.value, "created_at": u.created_at[:10]}
        for u in hits
    ]
    emit("tool", {"tool": "search_memory", "status": "done", "found": len(hits)})
    return ToolResult.ok(
        found=len(hits), hits=structured,
        _formatted=formatted,
        message=f"找到 {len(hits)} 条记忆",
    )


search_memory_tool = ToolSchema(
    name="search_memory",
    description=(
        "在用户的长期记忆中检索（跨项目：偏好/说过的事/决定/领域/坑）。"
        "当问题涉及用户本人——'我记得/我偏好/我之前说过/我的领域/你了解我吗/我上次怎么做的'——"
        "先用这个工具查记忆，再回答。检索不到就如实说，不要编造。"
        "scope: user/project（默认 user）；kind: fact/preference/decision/task/dead_end/insight/reference/style（可选）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要查的关键词/描述"},
            "scope": {"type": "string", "description": "user=跨项目个人记忆(默认)，project=项目内"},
            "kind": {"type": "string", "description": "可选类型过滤"},
            "limit": {"type": "integer", "description": "返回条数，默认5"},
        },
        "required": ["query"],
    },
    handler=_handle_search_memory,
    category="memory",
)
