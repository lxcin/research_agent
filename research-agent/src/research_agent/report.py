"""Project experience report — accumulated, typed project memory (Tier A).

During a project the agent records durable experience here (progress, decisions,
pitfalls, reusable procedures, tool candidates). It is project-scoped, human
readable/editable, and feeds the self-evolution promotion flow (evolve.py):
a human can promote an entry into a reusable skill / user tool / personal memory.

Stored at {workspace}/.research-agent/report.json; rendered to Markdown on demand.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

SECTIONS = ("progress", "decision", "pitfall", "procedure", "tool_candidate")
_SECTION_LABEL = {
    "progress": "进展",
    "decision": "决策",
    "pitfall": "踩坑",
    "procedure": "可复用流程",
    "tool_candidate": "候选工具",
}
STATUSES = ("open", "promoted", "dropped")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def report_path(workspace_dir: str) -> str:
    return os.path.join(workspace_dir, ".research-agent", "report.json")


def load(workspace_dir: str) -> dict:
    p = report_path(workspace_dir)
    if not os.path.isfile(p):
        return {"entries": []}
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {"entries": []}
        data.setdefault("entries", [])
        return data
    except Exception:
        return {"entries": []}


def save(workspace_dir: str, data: dict):
    p = report_path(workspace_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def add_entry(workspace_dir: str, section: str, text: str,
              source: dict | None = None) -> dict:
    section = (section or "").strip().lower()
    if section not in SECTIONS:
        raise ValueError(f"unknown section '{section}'; allowed: {SECTIONS}")
    text = (text or "").strip()
    if not text:
        raise ValueError("empty text")
    data = load(workspace_dir)
    entry = {
        "id": f"exp_{uuid.uuid4().hex[:8]}",
        "section": section,
        "text": text,
        "source": source or {},
        "status": "open",
        "promoted_to": "",
        "created_at": _now(),
    }
    data["entries"].append(entry)
    save(workspace_dir, data)
    return entry


def list_entries(workspace_dir: str, section: str | None = None,
                 status: str | None = None) -> list[dict]:
    entries = load(workspace_dir)["entries"]
    if section:
        entries = [e for e in entries if e.get("section") == section]
    if status:
        entries = [e for e in entries if e.get("status") == status]
    return entries


def get_entry(workspace_dir: str, entry_id: str) -> dict | None:
    for e in load(workspace_dir)["entries"]:
        if e.get("id") == entry_id:
            return e
    return None


def mark_status(workspace_dir: str, entry_id: str, status: str,
                promoted_to: str = "") -> bool:
    if status not in STATUSES:
        raise ValueError(f"unknown status '{status}'")
    data = load(workspace_dir)
    ok = False
    for e in data["entries"]:
        if e.get("id") == entry_id:
            e["status"] = status
            if promoted_to:
                e["promoted_to"] = promoted_to
            ok = True
    if ok:
        save(workspace_dir, data)
    return ok


def render_markdown(workspace_dir: str) -> str:
    entries = load(workspace_dir)["entries"]
    lines = ["# 项目经验报告", ""]
    for sec in SECTIONS:
        items = [e for e in entries
                 if e.get("section") == sec and e.get("status") != "dropped"]
        if not items:
            continue
        lines.append(f"## {_SECTION_LABEL[sec]}")
        for e in items:
            mark = " [已晋升]" if e.get("status") == "promoted" else ""
            lines.append(f"- ({e['id']}){mark} {e['text']}")
        lines.append("")
    return "\n".join(lines)
