"""Offline diagnostics scanner — aggregate recent run logs into findings.

Scans data_dir/logs/*.jsonl (newest N), computes per-run health summaries, tallies
faults by kind, and optionally runs a small model over low-quality sessions to
label semantic issues (contradiction / not_answering / placeholder).
Developer-facing; used by `research-agent diagnose` and report generation.
"""
import os

from research_agent.config import get_data_dir
from research_agent.diagnostics import summary as diag_summary


def _logs_dir(data_dir=None) -> str:
    base = data_dir or get_data_dir()
    return os.path.join(str(base), "logs")


def scan(data_dir=None, limit: int = 20, llm=None,
         semantic_window: int = 2000) -> dict:
    """Aggregate recent sessions. Returns {'sessions': [...], 'totals': {...}}."""
    logs_dir = _logs_dir(data_dir)
    files = diag_summary.list_logs(logs_dir)
    files = files[-limit:] if files else []

    sessions = []
    for path in files:
        events = diag_summary.load_log(path)
        s = diag_summary.summarize(events)
        s["log_file"] = os.path.basename(path)
        s["fault_texts"] = [_fault_text_of(f) for f in s.get("faults", [])]
        # semantic pass: skip sessions with no reply or no user input marker
        if llm is not None and _has_content(events):
            s["semantic_issues"] = _semantic_pass(llm, events, semantic_window)
        else:
            s["semantic_issues"] = []
        sessions.append(s)

    totals = {
        "sessions": len(sessions),
        "total_faults": sum(len(s.get("faults", [])) for s in sessions),
        "fault_kinds": _tally(sessions, "fault_kinds"),
        "semantic_issues": _tally(sessions, "semantic_issue_types"),
        "avg_success_rate": round(
            sum(s.get("success_rate", 0) for s in sessions) / len(sessions), 3
        ) if sessions else 0.0,
    }
    return {"sessions": sessions, "totals": totals}


def _has_content(events: list[dict]) -> bool:
    return any(e.get("event") == "user" or e.get("event") == "start" for e in events)


def _semantic_pass(llm, events: list[dict], window: int) -> list[dict]:
    """Small-model semantic review of one session's tail (user msgs + reply)."""
    from research_agent.llm import LLMProvider
    if not isinstance(llm, LLMProvider):
        return []
    tail = events[-window:] if len(events) > window else events
    user_msgs = [str(e.get("data", {}).get("text", ""))[:300]
                 for e in tail if e.get("event") == "user"]
    replies = [str(e.get("data", {}).get("text", ""))[:600]
               for e in tail if e.get("event") == "reply"]
    if not replies:
        return []
    prompt = (
        "以下是一段会话（用户消息 + 助手流式回复）。判断助手回复是否存在质量问题：\n"
        "只输出JSON数组，issue取 contradiction|not_answering|placeholder，无问题输出[]。\n"
        f"用户: {' / '.join(user_msgs[-3:]) or '(空)'}\n"
        f"回复: {' '.join(replies[-3:])}\nJSON:"
    )
    try:
        raw = llm.complete([{"role": "user", "content": prompt}], max_tokens=200)
    except Exception:
        return []
    return _parse_issues(raw)


def _parse_issues(raw: str) -> list[dict]:
    import json as _json
    import re as _re
    text = _re.sub(r"^```(?:json)?\s*", "", raw.strip()).rstrip("`").strip()
    try:
        data = _json.loads(text)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    valid = {"contradiction", "not_answering", "placeholder"}
    return [{"type": i.get("issue"), "detail": (i.get("detail") or "")[:200]}
            for i in data if isinstance(i, dict) and i.get("issue") in valid]


def _fault_text_of(fault: dict) -> str:
    from research_agent.diagnostics.monitor import _fault_text
    try:
        return _fault_text(fault)
    except Exception:
        return str(fault)


def _tally(sessions: list[dict], key: str) -> dict:
    from collections import Counter
    c: Counter = Counter()
    for s in sessions:
        if key == "semantic_issue_types":
            for it in s.get("semantic_issues", []):
                c[it.get("type", "unknown")] += 1
        elif key == "fault_kinds":
            for k, v in (s.get("fault_kinds") or {}).items():
                c[k] += v
    return dict(c)
