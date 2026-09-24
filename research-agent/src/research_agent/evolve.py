"""Self-evolution (phase 1): distill project experience into reusable skills.

Pipeline (each stage is recorded for the full-chain audit):

    classify -> build -> review(static) -> [host approval] -> write -> record

Only **skill** promotion is implemented here — cheap, safe, no code execution.
Plugin authoring is deliberately deferred: the capability-acquisition ladder says
prefer reuse of existing tools / register an MCP server first; a custom plugin is
a last resort (see docs/SELF_EVOLUTION.md).

User skills live in `data_dir/skills/*.md` (loaded alongside the repo `skills/`).
Every applied promotion appends a provenance record to `data_dir/evolution/records.jsonl`
so a fault can be traced back artifact -> record -> experience entry.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import uuid
from collections import Counter
from datetime import datetime, timezone

from research_agent import report as report_mod
from research_agent.config import get_data_dir
from research_agent.skill_loader import parse_skill_file

# ── capability-acquisition ladder (build vs buy) ──
_MCP_HINTS = ("mcp", "mcp server", "已有服务", "现成服务")
_CODE_HINTS = ("import ", "def ", "class ", "pip install", "endpoint", "api",
               "解析器", "算法", "计算", "函数", "写个脚本", "工具")
_PROC_HINTS = ("流程", "步骤", "先", "然后", "再", "清单", "checklist", "sop",
               "方法", "规范", "排查", "workflow")
_USER_HINTS = ("用户偏好", "我喜欢", "我习惯", "我通常", "风格")
_RULE_HINTS = ("必须", "始终", "一律", "禁止", "不得", "安全", "永远")

_MAX_SKILL_CHARS = 8000
_INJECTION_MARKERS = ("ignore previous", "忽略以上", "忽略前面", "system prompt",
                      "you are now", "开发者模式", "disregard")


def classify(text: str) -> dict:
    """Heuristic default target + reason (deterministic, LLM-free).

    Order follows the acquisition ladder: reuse -> mcp -> skill -> plugin -> memory
    -> rule -> keep. It is a *suggestion*; the user makes the final call.
    """
    t = (text or "").lower()
    if any(h in t for h in _MCP_HINTS):
        return {"target": "install_mcp", "reason": "已有现成 MCP/服务，优先接入而非自研"}
    if any(h in t for h in _PROC_HINTS):
        return {"target": "skill", "reason": "可复用流程/SOP，用 skill 即可（无需代码）"}
    if any(h in t for h in _USER_HINTS):
        return {"target": "memory", "reason": "关于用户本人的偏好/习惯"}
    if any(h in t for h in _RULE_HINTS):
        return {"target": "rule", "reason": "应始终生效的约束"}
    if any(h in t for h in _CODE_HINTS):
        return {"target": "plugin", "reason": "疑似需要新代码；先确认能否用现有工具/MCP 解决"}
    return {"target": "keep", "reason": "未识别为可复用资产，建议留在项目报告"}


# ── paths ──

def skills_dir() -> str:
    d = get_data_dir() / "skills"
    os.makedirs(d, exist_ok=True)
    return str(d)


def evolution_dir() -> str:
    d = get_data_dir() / "evolution"
    os.makedirs(d, exist_ok=True)
    return str(d)


def _records_path() -> str:
    return os.path.join(evolution_dir(), "records.jsonl")


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", (name or "").strip()).strip("-").lower()
    return s or "unnamed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── build ──

def build_skill(name: str, description: str, triggers, body: str,
                version: int = 1) -> tuple[str, str]:
    """Return (filename, markdown). Raises on missing required fields."""
    if not name or not name.strip():
        raise ValueError("skill requires a name")
    if not description or not description.strip():
        raise ValueError("skill requires a description")
    if not triggers:
        raise ValueError("skill requires non-empty triggers")
    if not body or not body.strip():
        raise ValueError("skill body is empty")
    trig = ", ".join('"%s"' % str(x).strip() for x in triggers if str(x).strip())
    md = (f"---\nname: {name.strip()}\ndescription: {description.strip()}\n"
          f"triggers: [{trig}]\nversion: {int(version)}\nenabled: true\n"
          f"---\n\n{body.strip()}\n")
    return f"{_slug(name)}.md", md


def _set_frontmatter_field(content: str, key: str, value: str) -> str:
    """Set/replace a scalar field inside the YAML front matter (line-level edit)."""
    m = re.match(r"^(---\s*\n)(.*?)(\n---\s*\n)", content, re.DOTALL)
    if not m:
        raise ValueError("missing YAML front matter")
    lines = m.group(2).splitlines()
    pat = re.compile(rf"^{re.escape(key)}\s*:")
    for i, ln in enumerate(lines):
        if pat.match(ln):
            lines[i] = f"{key}: {value}"
            break
    else:
        lines.append(f"{key}: {value}")
    return m.group(1) + "\n".join(lines) + m.group(3) + content[m.end():]


def _find_skill_file(name: str, sdir: str) -> str | None:
    path = os.path.join(sdir, f"{_slug(name)}.md")
    if os.path.isfile(path):
        return path
    for f in sorted(os.listdir(sdir)) if os.path.isdir(sdir) else []:
        if not f.endswith(".md"):
            continue
        sk = parse_skill_file(os.path.join(sdir, f))
        if sk and sk.name == name:
            return os.path.join(sdir, f)
    return None


def set_skill_enabled(name: str, enabled: bool, sdir: str | None = None) -> str:
    """Toggle a skill's `enabled` flag (writes the YAML front matter). Returns path."""
    sdir = sdir or skills_dir()
    path = _find_skill_file(name, sdir)
    if not path:
        raise FileNotFoundError(f"skill not found: {name}")
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    content = _set_frontmatter_field(content, "enabled", "true" if enabled else "false")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def list_skills(sdir: str | None = None) -> list[dict]:
    """User skills with state (enabled/version/triggers/used)."""
    sdir = sdir or skills_dir()
    usage = skill_usage()
    out = []
    for f in sorted(os.listdir(sdir)) if os.path.isdir(sdir) else []:
        if not f.endswith(".md"):
            continue
        path = os.path.join(sdir, f)
        sk = parse_skill_file(path)
        if not sk:
            continue
        out.append({"file": f, "name": sk.name, "enabled": sk.enabled,
                    "version": _read_version(path), "triggers": sk.triggers,
                    "used": usage.get(sk.name, 0)})
    return out


def _read_version(path: str) -> int:
    """Read the `version:` field from an existing skill file (default 1)."""
    try:
        with open(path, encoding="utf-8") as fh:
            head = fh.read(400)
        m = re.search(r"^version:\s*(\d+)", head, re.M)
        return int(m.group(1)) if m else 1
    except Exception:
        return 1


# ── review (deterministic static checks) ──

def review_skill(filename: str, content: str, sdir: str | None = None,
                 allow_existing: bool = False) -> dict:
    """Static review before writing. Returns {ok, issues, detail}.

    `allow_existing=True` permits updating an existing file (version bump) while
    still checking near-duplication against *other* skills.
    """
    sdir = sdir or skills_dir()
    issues: list[str] = []
    if len(content) > _MAX_SKILL_CHARS:
        issues.append(f"too large ({len(content)} > {_MAX_SKILL_CHARS} chars)")
    low = content.lower()
    for marker in _INJECTION_MARKERS:
        if marker in low:
            issues.append(f"prompt-injection marker: '{marker}'")
    if not re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL):
        issues.append("missing YAML front matter")
    path = os.path.join(sdir, filename)
    if os.path.exists(path) and not allow_existing:
        issues.append(f"skill already exists: {filename}")
    body = content.split("---", 2)[-1] if content.count("---") >= 2 else content
    for other in os.listdir(sdir):
        if not other.endswith(".md") or other == filename:
            continue
        try:
            with open(os.path.join(sdir, other), encoding="utf-8") as fh:
                other_body = fh.read().split("---", 2)[-1]
        except Exception:
            continue
        if difflib.SequenceMatcher(None, body.strip(), other_body.strip()).ratio() > 0.85:
            issues.append(f"near-duplicate of existing skill '{other}'")
            break
    return {"ok": not issues, "issues": issues,
            "detail": "; ".join(issues) if issues else "static checks passed"}


# ── provenance records ──

def add_record(entry_id: str, target: str, status: str, artifact: str = "",
               review: dict | None = None, reason: str = "",
               capabilities=None, approval: str = "",
               version: int = 0, parent: str = "") -> dict:
    rec = {
        "id": f"evo_{uuid.uuid4().hex[:8]}",
        "entry_id": entry_id,
        "target": target,
        "status": status,               # applied | rejected | rolled_back | dropped
        "artifact": artifact,
        "version": version,
        "parent": parent,               # previous record id (version chain)
        "review": review or {},
        "reason": reason,
        "capabilities": capabilities or [],
        "approval": approval,
        "ts": _now(),
    }
    with open(_records_path(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def _last_record_for(filename: str) -> str:
    """Id of the most recent applied record whose artifact is this filename."""
    for r in reversed(load_records()):
        if r.get("artifact", "").replace("\\", "/").endswith(filename) \
                and r.get("status") == "applied":
            return r.get("id", "")
    return ""


def load_records() -> list[dict]:
    p = _records_path()
    if not os.path.isfile(p):
        return []
    out = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def query_records(target: str | None = None, status: str | None = None,
                  text: str = "") -> list[dict]:
    recs = load_records()
    if target:
        recs = [r for r in recs if r.get("target") == target]
    if status:
        recs = [r for r in recs if r.get("status") == status]
    if text:
        tl = text.lower()
        recs = [r for r in recs
                if tl in json.dumps(r, ensure_ascii=False).lower()]
    return recs


def render_report() -> str:
    """Human-readable evolution report (full-chain audit summary)."""
    recs = load_records()
    lines = ["# 自进化报告", ""]
    lines.append(f"- 总记录: {len(recs)}")
    by_status: dict[str, int] = {}
    by_target: dict[str, int] = {}
    for r in recs:
        by_status[r.get("status", "?")] = by_status.get(r.get("status", "?"), 0) + 1
        by_target[r.get("target", "?")] = by_target.get(r.get("target", "?"), 0) + 1
    lines.append(f"- 状态: {by_status or '{}'}")
    lines.append(f"- 目标: {by_target or '{}'}")
    lines.append("")
    lines.append("## 记录明细")
    for r in recs[-50:]:
        lines.append(f"- [{r.get('status')}] {r.get('target')} "
                     f"{r.get('artifact') or ''} (entry={r.get('entry_id')}, "
                     f"review={ (r.get('review') or {}).get('detail', '') })")
    # available user skills (with usage counts)
    sdir = skills_dir()
    skills = [f for f in sorted(os.listdir(sdir)) if f.endswith(".md")]
    usage = skill_usage()
    lines += ["", "## 用户技能"]
    for f in skills:
        sk = parse_skill_file(os.path.join(sdir, f))
        name = sk.name if sk else f[:-3]
        state = "on" if (sk and sk.enabled) else "off"
        lines.append(f"- [{state}] {f} (used {usage.get(name, 0)})")
    return "\n".join(lines)


# ── evaluation closed-loop: usage tracking + utility (A/B) + prune ─────────────

def _usage_path() -> str:
    return os.path.join(evolution_dir(), "usage.jsonl")


def note_skill_used(name: str, trace: str = ""):
    """Record that a skill was injected into a run (for usage/utility stats)."""
    if not name:
        return
    try:
        with open(_usage_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"name": name, "trace": trace, "ts": _now()},
                                ensure_ascii=False) + "\n")
    except OSError:
        pass


def load_usage() -> list[dict]:
    p = _usage_path()
    if not os.path.isfile(p):
        return []
    out = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def skill_usage() -> dict:
    c: Counter = Counter()
    for u in load_usage():
        c[u.get("name", "")] += 1
    return {k: v for k, v in c.items() if k}


def utility_report() -> dict:
    """A/B evaluation: runs where a skill was used vs the rest (telemetry metrics)."""
    usage = load_usage()
    by_skill: dict[str, set] = {}
    for u in usage:
        t = u.get("trace", "")
        if t:
            by_skill.setdefault(u["name"], set()).add(t)
    try:
        from research_agent import telemetry
        runs = {r.get("trace"): r for r in telemetry.load_runs()}
    except Exception:
        runs = {}
    all_traces = set(runs)

    def _m(rows, fn):
        return round(sum(fn(r) for r in rows) / len(rows), 4) if rows else 0.0

    out = []
    for skill, traces in by_skill.items():
        used = [runs[t] for t in traces if t in runs]
        base = [runs[t] for t in (all_traces - traces)]
        out.append({
            "skill": skill, "uses": len(used),
            "used": {"tokens": _m(used, lambda r: r["tokens"]["total"]),
                     "cost_usd": _m(used, lambda r: r.get("cost_usd", 0)),
                     "wall_ms": _m(used, lambda r: r.get("wall_ms", 0)),
                     "completed": _m(used, lambda r: 1 if r.get("completed") else 0)},
            "baseline": {"tokens": _m(base, lambda r: r["tokens"]["total"]),
                         "cost_usd": _m(base, lambda r: r.get("cost_usd", 0)),
                         "wall_ms": _m(base, lambda r: r.get("wall_ms", 0)),
                         "completed": _m(base, lambda r: 1 if r.get("completed") else 0)},
        })
    return {"skills": out, "runs": len(runs), "usage_events": len(usage)}


def prune(min_uses: int = 0) -> dict:
    """List user skills with <= min_uses recorded uses (candidates for removal)."""
    used = skill_usage()
    sdir = skills_dir()
    files = [f for f in os.listdir(sdir) if f.endswith(".md")] if os.path.isdir(sdir) else []
    unused = []
    for f in files:
        sk = parse_skill_file(os.path.join(sdir, f))
        name = sk.name if sk else f[:-3]
        if used.get(name, 0) <= min_uses:
            unused.append(f)
    return {"unused": unused, "usage": used, "total_skills": len(files)}


def write_report(out_dir: str | None = None) -> dict:
    base = out_dir or evolution_dir()
    os.makedirs(base, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    md = render_report()
    md_path = os.path.join(base, f"report-{ts}.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    return {"md_path": md_path, "markdown": md}


# ── write (the single gated write point) ──

def write_skill(workspace_dir: str, name: str, description: str, triggers,
                body: str, entry_id: str = "", approval: str = "user") -> dict:
    """Build → review → write a user skill (versioned). Raises if review fails.

    Updating an existing skill by the same name bumps its version and records a
    `parent` link to the previous applied record.
    """
    slug = _slug(name)
    filename = f"{slug}.md"
    path = os.path.join(skills_dir(), filename)
    version, parent = 1, ""
    if os.path.exists(path):
        version = _read_version(path) + 1
        parent = _last_record_for(filename)

    filename, content = build_skill(name, description, triggers, body, version=version)
    review = review_skill(filename, content, allow_existing=True)
    if not review["ok"]:
        add_record(entry_id, "skill", "rejected", artifact=filename,
                   review=review, reason=review["detail"], version=version, parent=parent)
        raise ValueError(f"skill rejected by review: {review['detail']}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    parsed = parse_skill_file(path)
    if parsed is None:
        os.remove(path)
        add_record(entry_id, "skill", "rejected", artifact=filename,
                   review=review, reason="failed to parse as skill", version=version)
        raise ValueError("generated skill failed to parse")
    if entry_id:
        try:
            report_mod.mark_status(workspace_dir, entry_id, "promoted",
                                   promoted_to=filename)
        except Exception:
            pass
    return add_record(entry_id, "skill", "applied", artifact=path,
                      review=review, approval=approval, version=version, parent=parent)
