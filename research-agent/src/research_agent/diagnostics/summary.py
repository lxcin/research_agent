"""Session health summary — aggregate one run's JSONL log into a report dict.

Developer/maintainer facing. Reads data_dir/logs/{trace}.jsonl and computes
round counts, tool success/error rates, fault tally, context/latency hints.
"""
import json
import os
from collections import Counter
from datetime import datetime, timezone


def load_log(path: str) -> list[dict]:
    """Read a recorder JSONL file into a list of events. Never raises."""
    events = []
    if not path or not os.path.isfile(path):
        return events
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return events


def summarize(events: list[dict]) -> dict:
    """Compute a health summary from recorded events."""
    tool_start = Counter()
    tool_done = Counter()
    tool_error = Counter()
    faults = []
    reply_chars = 0
    tool_events = 0
    thinking_events = 0
    trace_id = ""
    workspace = ""
    chat = ""

    for ev in events:
        et = ev.get("event")
        data = ev.get("data", {}) or {}
        if ev.get("trace"):
            trace_id = ev["trace"]
        if ev.get("workspace"):
            workspace = ev["workspace"]
        if ev.get("chat"):
            chat = ev["chat"]

        if et == "fault":
            faults.append(data)
        elif et == "tool_start":
            tool_start[data.get("name", "")] += 1
        elif et == "tool_end":
            status = data.get("status", "")
            name = data.get("name", data.get("tool", ""))
            tool_events += 1
            if status == "success":
                tool_done[name] += 1
            elif status in ("error", "failed"):
                tool_error[name] += 1
        elif et == "thinking":
            thinking_events += 1
        elif et == "reply":
            reply_chars += len(str(data.get("text", "")))

    # Fault tally by kind
    fault_kinds = Counter(f.get("kind", "unknown") for f in faults)
    # Tool success rate
    attempted = sum(tool_start.values()) or 1
    succeeded = sum(tool_done.values())
    errors = sum(tool_error.values())

    return {
        "trace_id": trace_id,
        "workspace": workspace,
        "chat": chat,
        "event_count": len(events),
        "tool_attempts": attempted,
        "tool_success": succeeded,
        "tool_errors": errors,
        "success_rate": round(succeeded / attempted, 3) if attempted else 0.0,
        "tool_breakdown": {
            t: {"started": tool_start[t], "ok": tool_done[t], "error": tool_error[t]}
            for t in set(tool_start) | set(tool_done) | set(tool_error)
        },
        "fault_count": len(faults),
        "fault_kinds": dict(fault_kinds),
        "faults": faults,
        "thinking_events": thinking_events,
        "reply_chars": reply_chars,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def summarize_file(path: str) -> dict:
    return summarize(load_log(path))


def list_logs(logs_dir: str) -> list[str]:
    """Return *.jsonl paths sorted oldest → newest."""
    if not os.path.isdir(logs_dir):
        return []
    files = [os.path.join(logs_dir, f) for f in os.listdir(logs_dir) if f.endswith(".jsonl")]
    files.sort(key=os.path.getmtime)
    return files
