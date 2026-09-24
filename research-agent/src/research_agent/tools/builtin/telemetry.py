"""Telemetry plugin — runtime evaluation of real runs (read-only tools).

Exposes the collected usage/cost/latency/tool-path data to the agent:
  usage_report — aggregate token/cost/latency/tool stats over recent runs
  usage_query  — detail of one run by trace id

Collection itself happens in `research_agent.telemetry` (host-driven, only when
this plugin is enabled) — see docs/EVALUATION.md.
"""
import json

from research_agent.tools.schema import ToolSchema, ToolResult
from research_agent import telemetry


def _handle_usage_report(params, llm, state, emit) -> ToolResult:
    try:
        limit = int(params.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    rep = telemetry.report(limit=max(1, min(limit, 200)))
    emit("telemetry", {"stage": "usage_report", "runs": rep["runs"]})
    return ToolResult.ok(**rep, markdown=telemetry.render_markdown(rep))


def _handle_usage_query(params, llm, state, emit) -> ToolResult:
    trace = (params.get("trace_id") or "").strip()
    if not trace:
        return ToolResult.fail("Missing trace_id")
    rec = telemetry.query_run(trace)
    if rec is None:
        return ToolResult.fail(f"No run record for trace '{trace}'")
    emit("telemetry", {"stage": "usage_query", "trace": trace})
    return ToolResult.ok(**rec)


usage_report_tool = ToolSchema(
    name="usage_report",
    description=("运行时评测报告：汇总最近若干次真实运行的 token 消耗、费用、耗时、"
                 "工具调用与路径、费用归因。用于分析成本与效率。"),
    parameters={
        "type": "object",
        "properties": {"limit": {"type": "integer", "description": "统计最近多少次运行（默认20）"}},
        "required": [],
    },
    handler=_handle_usage_report,
    category="builtin",
)

usage_query_tool = ToolSchema(
    name="usage_query",
    description="按 trace_id 查询单次运行的评测明细：token/费用/时延/工具路径/阶段耗时。",
    parameters={
        "type": "object",
        "properties": {"trace_id": {"type": "string", "description": "运行的 trace id"}},
        "required": ["trace_id"],
    },
    handler=_handle_usage_query,
    category="builtin",
)
