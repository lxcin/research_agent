"""Full-chain framework audit: trace -> review -> score -> report.

Consumes the diagnostic event stream (`data_dir/logs/*.jsonl`) and produces a
**framework-effectiveness** scorecard — this judges the harness (tool health,
convergence/loops, completion, efficiency, stability), not the model.

Pure / deterministic: operates on recorded events, so every rule is unit-testable
by feeding synthetic event lists. No LLM, no network.

Dimensions (0-100 each) and weights:
  tool_health (0.30)  success/total tool calls
  convergence (0.25)  penalised by tool_loop / error_streak / empty_streak / search_exhausted
  completion  (0.25)  a usable final reply exists
  efficiency  (0.10)  penalised by redundant (same tool+params) calls / excessive calls
  stability   (0.10)  penalised by llm_unstable
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone

from research_agent.config import get_data_dir
from research_agent.diagnostics import summary as diag_summary

_WEIGHTS = {"tool_health": 0.30, "convergence": 0.25, "completion": 0.25,
            "efficiency": 0.10, "stability": 0.10}

_FAULT_LABEL = {
    "tool_loop": "工具重复调用（同名同参）",
    "error_streak": "工具连续失败",
    "empty_streak": "检索连续空转",
    "search_exhausted": "搜索次数耗尽",
    "llm_unstable": "LLM 不稳定",
    "no_response": "无可用回复",
}


def _logs_dir(data_dir=None) -> str:
    base = data_dir or get_data_dir()
    return os.path.join(str(base), "logs")


def grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    return "D"


# ── trace ───────────────────────────────────────────────────────────────────

def build_chain(events: list[dict]) -> dict:
    """Reconstruct one run's execution chain from its events."""
    trace = workspace = chat = ""
    calls: list[dict] = []
    pending: dict = {}
    faults: list[dict] = []
    has_reply = False
    reply_chars = 0
    confirms = file_changes = 0

    for ev in events:
        et = ev.get("event")
        d = ev.get("data") or {}
        trace = ev.get("trace") or trace
        workspace = ev.get("workspace") or workspace
        chat = ev.get("chat") or chat

        if et == "tool_start":
            c = {"name": d.get("name", ""), "input": str(d.get("input", ""))[:160],
                 "status": "pending"}
            calls.append(c)
            pending[d.get("id")] = c
        elif et == "tool_end":
            st = d.get("status", "")
            out = d.get("output")
            # A tool can return a ToolResult yet have failed underneath
            # (shell non-zero exit / success=false). Count those as errors.
            if st in ("success", "") and isinstance(out, dict):
                rc = out.get("returncode")
                if out.get("success") is False or (isinstance(rc, int) and rc != 0):
                    st = "error"
            c = pending.get(d.get("id"))
            if c is not None:
                c["status"] = st
            else:
                calls.append({"name": d.get("name", ""), "input": "", "status": st})
        elif et == "fault":
            faults.append(d)
        elif et == "reply":
            t = str(d.get("text", ""))
            if t.strip():
                has_reply = True
            reply_chars += len(t)
        elif et == "end":
            if str(d.get("final_response", "") or "").strip():
                has_reply = True
        elif et == "confirm_required":
            confirms += 1
        elif et == "file_change":
            file_changes += 1

    attempts = len(calls)
    errors = sum(1 for c in calls if c["status"] in ("error", "failed"))
    redundant = sum(1 for a, b in zip(calls, calls[1:])
                    if a["name"] == b["name"] and a["input"] == b["input"])
    kind_counts = Counter(f.get("kind", "unknown") for f in faults)

    return {
        "trace_id": trace, "workspace": workspace, "chat": chat,
        "event_count": len(events),
        "tool_calls": attempts, "tool_errors": errors, "redundant": redundant,
        "tool_names": dict(Counter(c["name"] for c in calls)),
        "fault_count": len(faults), "fault_kinds": dict(kind_counts),
        "has_reply": has_reply, "reply_chars": reply_chars,
        "confirms": confirms, "file_changes": file_changes,
        "steps": [f"{c['name']}{'✗' if c['status'] in ('error', 'failed') else ''}"
                  for c in calls],
    }


# ── review + score ────────────────────────────────────────────────────────────

def review_chain(ch: dict) -> list[str]:
    """Flag framework anti-patterns for this run."""
    issues = []
    for kind, cnt in ch["fault_kinds"].items():
        txt = _FAULT_LABEL.get(kind, kind)
        issues.append(f"{txt} ×{cnt}" if cnt > 1 else txt)
    if ch["redundant"]:
        issues.append(f"冗余重复调用 ×{ch['redundant']}（同名同参）")
    if ch["tool_errors"] and not any(k.endswith("streak") for k in ch["fault_kinds"]):
        issues.append(f"工具失败 ×{ch['tool_errors']}")
    if not ch["has_reply"]:
        issues.append("无可用最终回复")
    if ch["tool_calls"] > 12:
        issues.append(f"工具调用偏多 ×{ch['tool_calls']}")
    return issues


def score_chain(ch: dict) -> dict:
    attempts, errors = ch["tool_calls"], ch["tool_errors"]
    tool_health = 100.0 if attempts == 0 else round(100.0 * (attempts - errors) / attempts, 1)

    kc = ch["fault_kinds"]
    convergence = max(0.0, 100.0 - (kc.get("tool_loop", 0) * 20
                                    + kc.get("error_streak", 0) * 15
                                    + kc.get("empty_streak", 0) * 15
                                    + kc.get("search_exhausted", 0) * 10))
    completion = 100.0 if ch["has_reply"] else 0.0
    redundant_ratio = (ch["redundant"] / attempts) if attempts else 0.0
    efficiency = max(0.0, min(100.0, 100.0 - redundant_ratio * 100 - max(0, attempts - 8) * 2))
    stability = max(0.0, 100.0 - kc.get("llm_unstable", 0) * 20)

    dims = {"tool_health": tool_health, "convergence": round(convergence, 1),
            "completion": completion, "efficiency": round(efficiency, 1),
            "stability": round(stability, 1)}
    overall = round(sum(dims[k] * w for k, w in _WEIGHTS.items()), 1)
    return {**dims, "overall": overall, "grade": grade(overall)}


# ── audit (aggregate) ─────────────────────────────────────────────────────────

_ECON_WEIGHT = 0.15
_ECON = {"cost_avg_max": 0.05, "wall_p95_max": 60000.0, "tokens_avg_max": 30000.0}


def _economy_dim(rep: dict) -> dict:
    """Economy score from telemetry (cost/latency/tokens). None = no data → skip."""
    if not rep or not rep.get("runs"):
        return {"score": None, "runs": 0, "detail": "无遥测数据（跳过）"}
    avg, p95 = rep.get("avg", {}), rep.get("p95", {})
    score = 100.0
    if avg.get("cost_usd", 0) > _ECON["cost_avg_max"]:
        score -= min(50.0, (avg["cost_usd"] - _ECON["cost_avg_max"]) / _ECON["cost_avg_max"] * 20)
    if p95.get("wall_ms", 0) > _ECON["wall_p95_max"]:
        score -= min(30.0, (p95["wall_ms"] - _ECON["wall_p95_max"]) / _ECON["wall_p95_max"] * 15)
    if avg.get("tokens", 0) > _ECON["tokens_avg_max"]:
        score -= min(20.0, (avg["tokens"] - _ECON["tokens_avg_max"]) / _ECON["tokens_avg_max"] * 10)
    return {"score": max(0.0, round(score, 1)), "runs": rep["runs"],
            "avg_cost_usd": avg.get("cost_usd"), "p95_wall_ms": p95.get("wall_ms"),
            "avg_tokens": avg.get("tokens")}


def audit(limit: int = 20, data_dir=None, telemetry_rep: dict | None = None) -> dict:
    files = diag_summary.list_logs(_logs_dir(data_dir))
    files = files[-limit:] if files else []
    sessions = []
    for path in files:
        ch = build_chain(diag_summary.load_log(path))
        ch.update(score_chain(ch))
        ch["issues"] = review_chain(ch)
        ch["log_file"] = os.path.basename(path)
        sessions.append(ch)

    dims = list(_WEIGHTS)
    avg = {d: round(sum(s[d] for s in sessions) / len(sessions), 1) if sessions else 0.0
           for d in dims}
    base_overall = round(sum(s["overall"] for s in sessions) / len(sessions), 1) if sessions else 0.0
    if telemetry_rep is None:
        try:
            from research_agent import telemetry
            telemetry_rep = telemetry.report()
        except Exception:
            telemetry_rep = {}
    econ = _economy_dim(telemetry_rep)
    if econ.get("score") is None:
        overall = base_overall
    else:
        overall = round((1 - _ECON_WEIGHT) * base_overall + _ECON_WEIGHT * econ["score"], 1)
    issue_counts: Counter = Counter()
    for s in sessions:
        for i in s["issues"]:
            issue_counts[i.split("×")[0].strip()] += 1

    return {
        "sessions": sessions,
        "totals": {
            "sessions": len(sessions),
            "tool_calls": sum(s["tool_calls"] for s in sessions),
            "tool_errors": sum(s["tool_errors"] for s in sessions),
            "faults": sum(s["fault_count"] for s in sessions),
            "completed": sum(1 for s in sessions if s["has_reply"]),
            "dimensions": avg,
            "economy": econ.get("score"),
            "overall": overall,
            "grade": grade(overall),
        },
        "economy": econ,
        "issues": dict(issue_counts),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ── report ────────────────────────────────────────────────────────────────────

_DIM_ZH = {"tool_health": "工具健康", "convergence": "收敛/循环",
           "completion": "任务完成", "efficiency": "效率", "stability": "稳定性"}


def _recommend(totals: dict) -> list[str]:
    recs = []
    dims = totals.get("dimensions", {})
    if dims.get("completion", 100) < 90:
        recs.append("完成率偏低：检查 no_response / 达最大轮次场景，加强收敛收口。")
    if dims.get("convergence", 100) < 80:
        recs.append("收敛偏低：存在工具循环/连败/空转，检查工具选择与失败自纠。")
    if dims.get("tool_health", 100) < 90:
        recs.append("工具健康偏低：查看失败工具分布，修参数或补错误回灌提示。")
    if dims.get("efficiency", 100) < 80:
        recs.append("效率偏低：存在同名同参冗余调用，考虑去重或状态记忆。")
    if dims.get("stability", 100) < 90:
        recs.append("稳定性偏低：LLM 重试偏多，检查超时/重试退避配置。")
    return recs or ["各维度均健康。"]


def render_markdown(result: dict) -> str:
    t = result["totals"]
    lines = ["# 全链路审查评分报告", ""]
    lines.append(f"- 会话数: {t['sessions']} | 工具调用: {t['tool_calls']} | "
                 f"工具失败: {t['tool_errors']} | 故障: {t['faults']} | "
                 f"完成: {t['completed']}")
    lines.append(f"- **框架总分: {t['overall']} / 100（{t['grade']}）**")
    lines.append("")
    lines.append("## 维度评分")
    for d in _WEIGHTS:
        lines.append(f"- {_DIM_ZH[d]}: {t['dimensions'][d]}")
    econ = result.get("economy") or {}
    if econ.get("score") is not None:
        lines.append(f"- 经济性（成本/时延）: {econ['score']}（{econ['runs']} 次运行，"
                     f"均费 ${econ.get('avg_cost_usd')}，P95 {econ.get('p95_wall_ms')} ms）")
    lines.append("")
    if result.get("issues"):
        lines.append("## 高频问题")
        for k, c in sorted(result["issues"].items(), key=lambda kv: -kv[1]):
            lines.append(f"- {k}: {c}")
        lines.append("")
    lines.append("## 逐会话（追踪链）")
    for s in result["sessions"]:
        chain = " → ".join(s["steps"][:12]) or "(无工具调用)"
        lines.append(f"### {s['log_file']}  trace={s['trace_id']}")
        lines.append(f"- 分数 {s['overall']}（{s['grade']}）| 工具 {s['tool_calls']}"
                     f"/失败 {s['tool_errors']}/冗余 {s['redundant']} | 故障 {s['fault_count']}")
        lines.append(f"- 链: {chain}")
        for i in s["issues"]:
            lines.append(f"  - ⚠ {i}")
        lines.append("")
    lines.append("## 建议")
    for r in _recommend(t):
        lines.append(f"- {r}")
    return "\n".join(lines)


def write_report(result: dict, out_dir=None) -> dict:
    base = out_dir or os.path.join(str(get_data_dir()), "audit")
    os.makedirs(base, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    md_path = os.path.join(base, f"audit-{ts}.md")
    json_path = os.path.join(base, f"audit-{ts}.json")
    md = render_markdown(result)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    return {"md_path": md_path, "json_path": json_path, "markdown": md}
