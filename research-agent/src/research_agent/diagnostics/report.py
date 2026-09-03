"""Diagnostic report generation — write scan findings to markdown + JSON.

Output: data_dir/diagnostics/report-{ts}.md and .json. Developer/maintainer
facing (opened from CLI or /api/diagnostics).
"""
import json
import os
from datetime import datetime, timezone

from research_agent.config import get_data_dir

_KIND_ZH = {
    "empty_streak": "检索空转",
    "tool_loop": "工具重复调用",
    "error_streak": "工具连续失败",
    "search_exhausted": "搜索次数耗尽",
    "llm_unstable": "LLM 不稳定",
    "no_response": "空回复",
    "contradiction": "答复与工具结果矛盾",
    "not_answering": "答非所问",
    "placeholder": "空话/模板",
    "ungrounded_citation": "未读引用",
    "platitude": "空话套话",
    "too_short": "回复过短",
}


def _zh(kind: str) -> str:
    return _KIND_ZH.get(kind, kind)


def render_markdown(scan_result: dict) -> str:
    lines = ["# PaperPilot 诊断报告", ""]
    totals = scan_result.get("totals", {})
    lines.append(f"- 会话数: {totals.get('sessions', 0)}")
    lines.append(f"- 故障总数: {totals.get('total_faults', 0)}")
    lines.append(f"- 平均工具成功率: {totals.get('avg_success_rate', 0):.1%}")
    lines.append("")

    fk = totals.get("fault_kinds", {})
    if fk:
        lines.append("## 故障分类统计")
        for kind, cnt in sorted(fk.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {_zh(kind)} ({kind}): {cnt}")
        lines.append("")

    si = totals.get("semantic_issues", {})
    if si:
        lines.append("## 语义质量问题")
        for kind, cnt in sorted(si.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {_zh(kind)} ({kind}): {cnt}")
        lines.append("")

    lines.append("## 会话明细")
    for s in scan_result.get("sessions", []):
        lines.append(f"### {s.get('log_file', '?')}  trace={s.get('trace_id', '')}")
        lines.append(f"- 事件数 {s.get('event_count', 0)} | "
                     f"工具 {s.get('tool_attempts', 0)}次/成功{s.get('tool_success', 0)}/"
                     f"失败{s.get('tool_errors', 0)} | 成功率 {s.get('success_rate', 0):.0%}")
        for ft in s.get("fault_texts", []):
            lines.append(f"  - ⚠ {ft}")
        for it in s.get("semantic_issues", []):
            lines.append(f"  - ✗ {_zh(it.get('type'))}: {it.get('detail', '')}")
        lines.append("")
    return "\n".join(lines)


def write_report(scan_result: dict, data_dir=None) -> dict:
    """Write report-{ts}.md/.json. Returns {md_path, json_path, markdown}."""
    base = data_dir or get_data_dir()
    out_dir = os.path.join(str(base), "diagnostics")
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    md_path = os.path.join(out_dir, f"report-{ts}.md")
    json_path = os.path.join(out_dir, f"report-{ts}.json")
    markdown = render_markdown(scan_result)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(scan_result, f, ensure_ascii=False, indent=2)
    return {"md_path": md_path, "json_path": json_path, "markdown": markdown}


def latest_report(data_dir=None) -> str | None:
    """Path to most recent report md, if any."""
    base = data_dir or get_data_dir()
    out_dir = os.path.join(str(base), "diagnostics")
    if not os.path.isdir(out_dir):
        return None
    mds = [os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith(".md")]
    if not mds:
        return None
    return max(mds, key=os.path.getmtime)
