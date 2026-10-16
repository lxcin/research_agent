"""plan tools — structured task planning (plan-as-memory write/recall).

`plan_update` registers the current task's plan as structured steps BEFORE
execution (not prose), so multi-step work is decomposed, independent steps can be
delegated in parallel, and progress is explicit. `plan_search` recalls similar
past plans (episodic recall). The plan is persisted at run end (see agent.py).
"""
from research_agent.tools.schema import ToolSchema, ToolResult
from research_agent import plan as plan_mod
from research_agent.plan_executor import execute_plan
from research_agent.plan_state import current_plan, set_current_plan


def _coerce_steps(raw) -> list:
    steps = []
    for s in (raw or []):
        if isinstance(s, str):
            step, status, deps, result, tools, reason = s.strip(), "pending", [], "", [], ""
        elif isinstance(s, dict):
            step = (s.get("step") or s.get("content") or "").strip()
            status = str(s.get("status", "pending")).strip().lower()
            deps = s.get("depends_on") or []
            result = str(s.get("result", "")).strip()
            tools = s.get("tools") or []
            reason = str(s.get("reason", "")).strip()
        else:
            continue
        if not step:
            continue
        if status not in plan_mod.STATUSES:
            status = "pending"
        if not isinstance(deps, list):
            deps = []
        deps = [int(d) for d in deps if isinstance(d, (int, float))]
        if not isinstance(tools, list):
            tools = []
        tools = [str(t) for t in tools if isinstance(t, str)]
        steps.append(plan_mod.PlanStep(step=step, status=status,
                                       depends_on=deps, result=result,
                                       tools=tools, reason=reason))
    return steps


def _handle_plan_update(params: dict, llm, state, emit) -> ToolResult:
    goal = (params.get("goal") or "").strip()
    steps = _coerce_steps(params.get("steps"))
    if not steps:
        return ToolResult.fail("plan_update requires at least one step")
    decisions = [str(x).strip() for x in (params.get("decisions") or []) if str(x).strip()]
    outcome = (params.get("outcome") or "").strip()

    plan = plan_mod.Plan(
        goal=goal, steps=steps, decisions=decisions, outcome=outcome,
        chat_id=getattr(state, "active_chat_id", "") or "",
        workspace_dir=getattr(state, "workspace_dir", "") or "",
    )
    set_current_plan(state, plan)

    emit("plan", {"goal": goal, "total": len(steps),
                  "done": sum(1 for s in steps if s.status == "done")})
    return ToolResult.ok(
        goal=goal,
        total=len(steps),
        steps=[{"step": s.step, "status": s.status,
                "depends_on": s.depends_on, "reason": s.reason} for s in steps],
        message=plan.render(),
        hint="按计划逐步执行；每完成一步，用 plan_update 将该步 status 改为 done。",
    )


def _handle_plan_search(params: dict, llm, state, emit) -> ToolResult:
    query = (params.get("query") or "").strip()
    if not query:
        return ToolResult.fail("Missing query")
    try:
        limit = max(1, min(int(params.get("limit", 3)), 10))
    except (TypeError, ValueError):
        limit = 3

    ws = getattr(state, "workspace_dir", "") or ""
    scope = str(params.get("scope", "user")).strip().lower()
    cross = scope != "project"
    plans = plan_mod.search_plans(ws, query, limit=limit, cross_project=cross)
    if not plans:
        emit("plan", {"tool": "plan_search", "status": "empty"})
        return ToolResult.ok(found=0, plans=[],
                             message="没有找到历史同类任务的计划")

    def _project(p) -> str:
        import os as _os
        return _os.path.basename((p.workspace_dir or "").rstrip("/\\")) or "?"

    structured = [
        {"goal": p.goal, "created_at": (p.created_at or "")[:10],
         "project": _project(p),
         "decisions": list(p.decisions), "outcome": p.outcome,
         "steps": [f"[{s.status}] {s.step}" for s in p.steps]}
        for p in plans
    ]

    def _fmt(r: dict) -> str:
        lines = [f"- {r['goal']} ({r['created_at']}, 项目:{r['project']})"]
        lines += [f"    {st}" for st in r["steps"]]
        lines += [f"    决策: {d}" for d in r["decisions"]]
        if r["outcome"]:
            lines.append(f"    结果: {r['outcome']}")
        return "\n".join(lines)

    body = "\n\n".join(_fmt(r) for r in structured)
    emit("plan", {"tool": "plan_search", "status": "done", "found": len(plans)})
    return ToolResult.ok(found=len(plans), plans=structured,
                         message="历史同类任务情景（跨项目）：\n" + body)


def _handle_plan_execute(params: dict, llm, state, emit) -> ToolResult:
    plan = current_plan(state)
    if plan is None:
        return ToolResult.fail("没有已登记的计划，请先用 plan_update 登记")
    if not plan.steps:
        return ToolResult.fail("计划为空")

    try:
        max_rounds = int(params.get("max_rounds", 3) or 3)
    except (TypeError, ValueError):
        max_rounds = 3
    try:
        max_parallel = max(1, min(int(params.get("max_parallel", 6) or 6), 8))
    except (TypeError, ValueError):
        max_parallel = 6

    def dispatch(idx, step):
        from research_agent.tools import get_registry
        from research_agent.tools.subagent import spawn_subagent
        r = spawn_subagent([{"task": step.step, "tools": step.tools or None}],
                           llm, state, get_registry(),
                           max_rounds=max_rounds, emit=emit)
        ok = int(r.get("completed", 0)) > 0
        summary = ""
        if r.get("summaries"):
            summary = r["summaries"][0].get("summary", "") or ""
        return {"success": ok, "summary": summary,
                "error": "" if ok else "subtask failed"}

    def _on_batch(p):
        set_current_plan(state, p)
        emit("plan", {"tool": "plan_execute", "status": "batch",
                      "done": sum(1 for s in p.steps if s.status == "done"),
                      "total": len(p.steps)})

    plan = execute_plan(plan, dispatch, max_parallel=max_parallel, on_batch=_on_batch)
    set_current_plan(state, plan)

    done = sum(1 for s in plan.steps if s.status == "done")
    blocked = sum(1 for s in plan.steps if s.status == "blocked")
    return ToolResult.ok(
        total=len(plan.steps), done=done, blocked=blocked,
        results=[{"step": s.step, "status": s.status, "result": s.result}
                 for s in plan.steps],
        message=f"计划执行完成：{done}/{len(plan.steps)} 步成功" +
                (f"，{blocked} 步受阻" if blocked else "") + "\n" + plan.render(),
    )


plan_update_tool = ToolSchema(
    usage="多步骤任务登记结构化计划，包含依赖与关键决策；需要自动调度时使用 plan_execute。",
    name="plan_update",
    description=(
        "登记/更新当前任务的执行计划（结构化步骤，不是散文）。"
        "多步、可分解、需并行子任务或跨文件的任务，开始前先用本工具列出步骤，"
        "然后逐步执行，每完成一步把该步 status 改为 done；简单单步任务不必调用。"
        "每次传入完整步骤列表（覆盖式更新）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "任务目标（一句话）"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step": {"type": "string", "description": "该步做什么"},
                        "status": {"type": "string",
                                   "description": "pending|in_progress|done|blocked"},
                        "depends_on": {"type": "array", "items": {"type": "integer"},
                                       "description": "依赖的前置步骤序号（1基），无则留空"},
                        "result": {"type": "string", "description": "该步结果/产出（完成后填）"},
                        "tools": {"type": "array", "items": {"type": "string"},
                                  "description": "plan_execute 执行该步时允许子代理使用的工具名；省略=仅只读"},
                        "reason": {"type": "string", "description": "该步为什么这么做/背后的决策（why）"},
                    },
                    "required": ["step"],
                },
            },
            "decisions": {"type": "array", "items": {"type": "string"},
                          "description": "本次任务的关键决策及原因（why），可选"},
            "outcome": {"type": "string", "description": "任务结果/产出，可选"},
        },
        "required": ["steps"],
    },
    handler=_handle_plan_update,
    category="plan",
)


plan_search_tool = ToolSchema(
    usage="开始可能做过的任务时先提炼检索词查历史情景；召回是参考证据，不应覆盖当前用户约束。",
    name="plan_search",
    description=(
        "检索历史同类任务的情景记忆（做法/决策/结果）。**默认跨项目**："
        "开始一个可能做过的任务前，自己提炼检索词调用本工具，看看过去（含其他项目）怎么做、"
        "为什么这么做、踩了什么坑，避免重复探索。scope=project 时只看当前项目。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "任务目标/关键词"},
            "limit": {"type": "integer", "description": "返回条数，默认3"},
            "scope": {"type": "string",
                      "description": "user=跨项目(默认) | project=仅当前项目"},
        },
        "required": ["query"],
    },
    handler=_handle_plan_search,
    category="plan",
)


plan_execute_tool = ToolSchema(
    name="plan_execute",
    description=(
        "按已登记的计划自动执行：按依赖顺序调度，无依赖的步骤并行派发子代理；"
        "先 plan_update 登记计划，再 plan_execute 执行。"
        "每步可用 step.tools 指定子代理允许的工具（省略=仅只读）；"
        "失败的步骤及其下游会标记 blocked。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "max_parallel": {"type": "integer", "description": "最大并行步骤数（默认6）"},
            "max_rounds": {"type": "integer", "description": "每个子代理最大轮次（默认3）"},
        },
        "required": [],
    },
    handler=_handle_plan_execute,
    category="plan",
)
