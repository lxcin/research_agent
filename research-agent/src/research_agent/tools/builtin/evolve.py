"""Self-evolution plugin (phase 1): record project experience and promote it
into a reusable **skill** (human-approved).

Tools:
  record_experience  — append a typed entry to the project experience report
  classify_experience— suggest a promotion target (build/buy ladder)
  propose_skill      — build + review + write a user skill (requires approval)
  list_experience    — read the project experience report
  list_skills        — list currently available user skills

Only skills are produced here. Plugin authoring is deferred on purpose — reuse
existing tools / register MCP first (see docs/SELF_EVOLUTION.md).
"""
import os

from research_agent.tools.schema import ToolSchema, ToolResult
from research_agent import report as report_mod
from research_agent import evolve


def _ws(state) -> str:
    ws = getattr(state, "workspace_dir", "")
    if ws and os.path.isdir(ws):
        return ws
    from research_agent.config import get_data_dir
    ws = str(get_data_dir() / "workspaces" / "default")
    os.makedirs(ws, exist_ok=True)
    return ws


def _handle_record(params, llm, state, emit) -> ToolResult:
    section = params.get("section", "")
    text = params.get("text", "")
    try:
        entry = report_mod.add_entry(_ws(state), section, text)
    except ValueError as e:
        return ToolResult.fail(str(e))
    emit("evolve", {"stage": "record", "entry_id": entry["id"], "section": section})
    return ToolResult.ok(entry_id=entry["id"], section=section, recorded=True)


def _handle_classify(params, llm, state, emit) -> ToolResult:
    text = params.get("text", "")
    if not text.strip():
        return ToolResult.fail("Missing text")
    verdict = evolve.classify(text)
    emit("evolve", {"stage": "classify", **verdict})
    return ToolResult.ok(**verdict)


def _handle_propose_skill(params, llm, state, emit) -> ToolResult:
    name = params.get("name", "")
    description = params.get("description", "")
    triggers = params.get("triggers") or []
    body = params.get("body", "")
    entry_id = params.get("entry_id", "")
    try:
        rec = evolve.write_skill(_ws(state), name=name, description=description,
                                 triggers=triggers, body=body, entry_id=entry_id)
    except ValueError as e:
        emit("evolve", {"stage": "propose_skill", "status": "rejected", "error": str(e)[:150]})
        return ToolResult.fail(str(e))
    emit("evolve", {"stage": "propose_skill", "status": "applied",
                    "artifact": rec["artifact"]})
    return ToolResult.ok(applied=True, artifact=rec["artifact"], record_id=rec["id"],
                         hint="用户技能已写入 data_dir/skills，下次会话按 trigger 生效")


def _handle_list_experience(params, llm, state, emit) -> ToolResult:
    section = params.get("section", "") or None
    entries = report_mod.list_entries(_ws(state), section=section)
    emit("evolve", {"stage": "list_experience", "count": len(entries)})
    return ToolResult.ok(count=len(entries), entries=entries,
                         markdown=report_mod.render_markdown(_ws(state)))


def _handle_list_skills(params, llm, state, emit) -> ToolResult:
    skills = evolve.list_skills()
    return ToolResult.ok(count=len(skills), skills=skills)


record_experience_tool = ToolSchema(
    name="record_experience",
    description=("把项目开发中值得沉淀的经验记录到项目报告。section 取 "
                 "progress/decision/pitfall/procedure/tool_candidate；"
                 "procedure=可复用流程，pitfall=踩坑，tool_candidate=需要的工具能力。"),
    parameters={
        "type": "object",
        "properties": {
            "section": {"type": "string",
                        "enum": list(report_mod.SECTIONS)},
            "text": {"type": "string", "description": "一句话、自包含的经验描述"},
        },
        "required": ["section", "text"],
    },
    handler=_handle_record,
    category="builtin",
)

classify_experience_tool = ToolSchema(
    name="classify_experience",
    description="判断一条经验更适合晋升成 skill / 接入 MCP / 记忆 / 规则，还是留在报告。只给建议。",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "经验文本"}},
        "required": ["text"],
    },
    handler=_handle_classify,
    category="builtin",
)

propose_skill_tool = ToolSchema(
    name="propose_skill",
    description=("把一条可复用流程沉淀为用户技能（写入 data_dir/skills，跨项目生效）。"
                 "需人工审批；要提供 name/description/triggers/body。"),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "技能名（英文标识）"},
            "description": {"type": "string", "description": "一句话说明何时用"},
            "triggers": {"type": "array", "items": {"type": "string"},
                         "description": "触发关键词列表"},
            "body": {"type": "string", "description": "技能正文（Markdown 流程/清单）"},
            "entry_id": {"type": "string", "description": "可选：来源经验条目 id"},
        },
        "required": ["name", "description", "triggers", "body"],
    },
    handler=_handle_propose_skill,
    category="builtin",
)

list_experience_tool = ToolSchema(
    name="list_experience",
    description="查看项目经验报告（可按 section 过滤）。用于回顾已沉淀的经验。",
    parameters={
        "type": "object",
        "properties": {
            "section": {"type": "string", "enum": list(report_mod.SECTIONS)},
        },
        "required": [],
    },
    handler=_handle_list_experience,
    category="builtin",
)

list_skills_tool = ToolSchema(
    name="list_skills",
    description="列出当前可用的用户技能（data_dir/skills 下的 SKILL.md）。",
    parameters={"type": "object", "properties": {}, "required": []},
    handler=_handle_list_skills,
    category="builtin",
)
