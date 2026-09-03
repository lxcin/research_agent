"""Fault → Tier B memory feedback (E.5).

Repeated/typical faults discovered by diagnostics are distilled into
DEAD_END memory units so future sessions can avoid repeating the same mistake
(ROUTE-triggered retrieval can surface them as reminders).
"""
from research_agent.diagnostics.monitor import _fault_text
from research_agent.memory.models import MemoryUnit, MemoryKind, MemoryScope

# fault kinds worth persisting as reusable lessons.
PERSIST_KINDS = {
    "tool_loop", "error_streak", "empty_streak",
    "search_exhausted", "no_response",
}

_MIN_COUNT_TO_PERSIST = 2


def _lesson_text(fault: dict) -> str:
    kind = fault.get("kind", "")
    tool = fault.get("tool", "")
    reason = _fault_text(fault)
    if kind == "tool_loop":
        return (f"用户项目曾重复调用工具 {tool} 且参数不变多次无效（lesson: 相同参数不重复调用，"
                f"先分析前次失败再换策略）")
    if kind == "error_streak":
        return (f"用户项目曾遇到工具 {tool} 连续失败（lesson: 失败后先读 stderr 诊断再重试，"
                f"不要盲目重试相同命令）")
    if kind == "empty_streak":
        return (f"用户项目曾连续多次检索返回空（lesson: 本地无结果立即转 search_papers 或换关键词，"
                f"不要重复相同检索）")
    if kind == "search_exhausted":
        return "用户项目曾耗尽搜索配额（lesson: 搜索次数有限，先精炼关键词再搜，搜到就 read_paper）"
    if kind == "no_response":
        return "用户项目曾出现会话结束但无有效回复（lesson: 工具失败后应换方式回答或明确告知，不静默）"
    return f"曾发生故障: {reason}"


def ingest_fault_lessons(fault_kinds: dict, mgr=None, min_count: int = _MIN_COUNT_TO_PERSIST) -> int:
    """Persist recurring fault kinds as DEAD_END units. Returns count written.

    fault_kinds: {kind: count} from diagnostics totals. Only kinds above
    min_count and in PERSIST_KINDS are written (dedup'd by identical text).
    """
    if not fault_kinds:
        return 0
    if mgr is None:
        from research_agent.memory import get_manager
        mgr = get_manager()

    written = 0
    for kind, count in fault_kinds.items():
        if count < min_count or kind not in PERSIST_KINDS:
            continue
        text = _lesson_text({"kind": kind})
        # avoid duplicate lesson already stored
        existing = mgr.list_units(scope=MemoryScope.USER, kind=MemoryKind.DEAD_END, limit=100)
        if any(u.text == text for u in existing):
            continue
        mgr.write(MemoryUnit(
            text=text,
            kind=MemoryKind.DEAD_END,
            importance=0.6,
            scope=MemoryScope.USER,
            source={"via": "diagnostics", "fault": kind, "occurrences": count},
        ))
        written += 1
    return written
