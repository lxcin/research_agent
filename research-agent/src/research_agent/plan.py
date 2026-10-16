"""Task planning artifacts — plan-as-memory.

A structured plan is produced by the agent BEFORE multi-step work, drives the
execution order (independent steps can be delegated in parallel via
spawn_subagent), and is persisted per task as a reusable episodic record. Plans
live under the project data dir (never the git workspace), so they do not show up
as file-change proposals.

Two tools use this module (see tools/builtin/plan.py):
  - plan_update : write/refresh the current task's plan (plan.current component).
  - plan_search : recall similar past plans (episodic recall).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

STATUSES = ("pending", "in_progress", "done", "blocked")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class PlanStep:
    step: str
    status: str = "pending"
    depends_on: list[int] = field(default_factory=list)
    result: str = ""
    tools: list[str] = field(default_factory=list)   # allowed tools when delegated
    reason: str = ""                                  # why this step / the decision behind it


@dataclass
class Plan:
    goal: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)   # key decisions (the "why")
    outcome: str = ""                                    # what resulted
    tools_used: list[str] = field(default_factory=list)  # tools actually invoked
    created_at: str = ""
    updated_at: str = ""
    chat_id: str = ""
    workspace_dir: str = ""
    embedding: list[float] = field(default_factory=list)  # index vector (vector recall)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = _now()
        self.updated_at = _now()

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "steps": [asdict(s) for s in self.steps],
            "decisions": list(self.decisions),
            "outcome": self.outcome,
            "tools_used": list(self.tools_used),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "chat_id": self.chat_id,
            "workspace_dir": self.workspace_dir,
            "embedding": list(self.embedding),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        steps = [
            PlanStep(
                step=str(s.get("step", "")),
                status=str(s.get("status", "pending")),
                depends_on=list(s.get("depends_on", []) or []),
                result=str(s.get("result", "")),
                tools=list(s.get("tools", []) or []),
                reason=str(s.get("reason", "")),
            )
            for s in (d.get("steps") or [])
            if isinstance(s, dict)
        ]
        try:
            embedding = [float(x) for x in (d.get("embedding") or [])]
        except (TypeError, ValueError):
            embedding = []
        return cls(
            goal=str(d.get("goal", "")),
            steps=steps,
            decisions=[str(x) for x in (d.get("decisions") or [])],
            outcome=str(d.get("outcome", "")),
            tools_used=[str(x) for x in (d.get("tools_used") or [])],
            created_at=str(d.get("created_at", "")),
            updated_at=str(d.get("updated_at", "")),
            chat_id=str(d.get("chat_id", "")),
            workspace_dir=str(d.get("workspace_dir", "")),
            embedding=embedding,
        )

    def render(self) -> str:
        marks = {"pending": "[ ]", "in_progress": "[~]",
                 "done": "[x]", "blocked": "[!]"}
        lines = [f"计划: {self.goal or '(未命名)'}"]
        for i, s in enumerate(self.steps, 1):
            dep = f" (依赖 {s.depends_on})" if s.depends_on else ""
            why = f" — {s.reason}" if s.reason else ""
            lines.append(f"{i}. {marks.get(s.status, '[ ]')} {s.step}{dep}{why}")
        if self.decisions:
            lines.append("决策:")
            lines += [f"  - {d}" for d in self.decisions]
        if self.outcome:
            lines.append(f"结果: {self.outcome}")
        return "\n".join(lines)


# ── Persistence (SQLite + numpy — lightweight local episodic DB; no server) ──
#
# One file under the data dir holds every project's episodes; embeddings are
# float32 BLOBs. Recall is vector-first via a cached numpy matrix multiply, with
# a jieba-token keyword fallback. No external DB / no ANN index — fine to the
# tens-of-thousands of episodes.

_DB_LOCK = threading.Lock()
_VERSION = 0
_CACHE: dict = {"key": None, "records": []}


def _db_path() -> str:
    from research_agent.config import get_data_dir
    return str(get_data_dir() / "episodes.db")


def _connect():
    conn = sqlite3.connect(_db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS episodes ("
        " id TEXT PRIMARY KEY, workspace TEXT, created_at TEXT, updated_at TEXT,"
        " payload TEXT, tokens TEXT, embedding BLOB, dim INTEGER)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ep_created ON episodes(created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ep_ws ON episodes(workspace)")
    conn.commit()


def _bump():
    global _VERSION
    _VERSION += 1


def reset_for_tests():
    """Clear the in-memory record cache (test hook)."""
    _CACHE["key"] = None
    _CACHE["records"] = []
    _bump()


def _searchable_text(p: Plan) -> str:
    parts = [p.goal, " ".join(p.decisions), p.outcome, " ".join(p.tools_used),
             " ".join(s.step for s in p.steps)]
    return " ".join(x for x in parts if x).strip()


def _embed(text: str) -> list[float]:
    """Index vector for vector recall; [] when the embedding layer is unavailable."""
    if not text:
        return []
    try:
        from research_agent import embedding as _emb
        if not _emb.is_available():
            return []
        arr = _emb.encode_documents([text])
        if arr is None:
            return []
        return [float(x) for x in arr[0]]
    except Exception:
        return []


def _plan_row(plan: Plan) -> tuple:
    text = _searchable_text(plan)
    tokens = " ".join(sorted(_tokens(text)))
    emb_bytes = None
    dim = 0
    vec = plan.embedding or _embed(text)
    if vec:
        arr = np.asarray(vec, dtype=np.float32)
        emb_bytes = arr.tobytes()
        dim = int(arr.shape[0])
    payload = plan.to_dict()
    payload["embedding"] = []
    return (uuid.uuid4().hex, plan.workspace_dir or "", plan.created_at, plan.updated_at,
            json.dumps(payload, ensure_ascii=False), tokens, emb_bytes, dim)


def _migrate_json(conn):
    """One-time import of legacy JSON episodes (projects/*/plans/*.json)."""
    try:
        if conn.execute("SELECT 1 FROM episodes LIMIT 1").fetchone() is not None:
            return
        from research_agent.config import get_data_dir
        root = get_data_dir() / "projects"
        for f in root.glob("*/plans/*.json"):
            try:
                with open(f, encoding="utf-8") as fh:
                    p = Plan.from_dict(json.load(fh))
                conn.execute(
                    "INSERT OR REPLACE INTO episodes (id, workspace, created_at,"
                    " updated_at, payload, tokens, embedding, dim) VALUES (?,?,?,?,?,?,?,?)",
                    _plan_row(p))
            except Exception:
                continue
        conn.commit()
    except Exception:
        pass


def save_plan(plan: Plan) -> str:
    """Insert an episode row; returns the row id ("" on failure). Never raises."""
    try:
        row = _plan_row(plan)
        with _DB_LOCK:
            conn = _connect()
            try:
                _ensure_schema(conn)
                _migrate_json(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO episodes (id, workspace, created_at,"
                    " updated_at, payload, tokens, embedding, dim) VALUES (?,?,?,?,?,?,?,?)",
                    row)
                conn.commit()
            finally:
                conn.close()
        _bump()
        return row[0]
    except Exception:
        return ""


def _load_records() -> list[dict]:
    """All episodes (newest first), cached in memory until the next write."""
    key = (_db_path(), _VERSION)
    if _CACHE["key"] == key:
        return _CACHE["records"]
    recs: list[dict] = []
    try:
        conn = _connect()
        try:
            _ensure_schema(conn)
            _migrate_json(conn)
            cur = conn.execute(
                "SELECT id, workspace, created_at, payload, tokens, embedding"
                " FROM episodes ORDER BY created_at DESC")
            for r in cur:
                emb = (np.frombuffer(r["embedding"], dtype=np.float32)
                       if r["embedding"] else None)
                recs.append({
                    "id": r["id"], "workspace": r["workspace"] or "",
                    "created_at": r["created_at"] or "",
                    "plan": json.loads(r["payload"]),
                    "tokens": set((r["tokens"] or "").split()),
                    "emb": emb,
                })
        finally:
            conn.close()
    except Exception:
        recs = []
    _CACHE["key"] = key
    _CACHE["records"] = recs
    return recs


def list_plans(workspace_dir: str, limit: int = 20) -> list[Plan]:
    """Most recent episodes for one workspace."""
    ws = os.path.abspath(workspace_dir or "")
    out = [r for r in _load_records() if os.path.abspath(r["workspace"]) == ws]
    return [Plan.from_dict(r["plan"]) for r in out[:limit]]


def list_all_plans(limit: int = 500) -> list[Plan]:
    """Episodes across ALL projects (newest first) — for cross-project recall."""
    return [Plan.from_dict(r["plan"]) for r in _load_records()[:limit]]


def episode_from_run(*, user_input: str = "", final_response: str = "",
                     tool_path: list[str] | None = None,
                     workspace_dir: str = "", chat_id: str = "") -> Plan:
    """Build an automatic episode record for a run that had no explicit plan.

    Heuristic (no LLM): goal from the user's first line, the tool path that was
    actually used, and a short outcome from the final response.
    """
    first = ""
    for line in (user_input or "").strip().splitlines():
        if line.strip():
            first = line.strip()
            break
    tools: list[str] = []
    for t in (tool_path or []):
        if t and t not in tools:
            tools.append(t)
    return Plan(
        goal=first[:200] or "(未命名任务)",
        steps=[PlanStep(step=f"调用 {t}", status="done") for t in tools],
        decisions=[],
        outcome=(final_response or "").strip()[:300],
        tools_used=tools,
        workspace_dir=workspace_dir,
        chat_id=chat_id,
    )


# ── LLM episode distillation (async write path) ──

EPISODE_EXTRACT_PROMPT = """你是"任务情景记忆"提炼器。把这一回合的任务归纳成可复用的情景记录。

规则：
1. 只提任务级、可复用的信息：目标、关键决策(含原因)、主要步骤及结果、最终结果。
2. 不要提取工具输出/命令/代码细节/闲聊。
3. decisions 每条写成 {{"decision": "...", "reason": "..."}}；
   steps 每条 {{"step": "...", "status": "done|failed", "result": "..."}}。
4. 没有可复用价值就输出 {{}}。

只输出 JSON，不要 markdown：
{{"goal":"...","decisions":[{{"decision":"...","reason":"..."}}],"steps":[{{"step":"...","status":"done","result":"..."}}],"outcome":"..."}}

用户: {user}
助手回复: {response}
用过的工具: {tools}
相关对话:
{conversation}"""


def _parse_episode_json(raw: str) -> dict | None:
    txt = re.sub(r"^```(?:json)?\s*", "", (raw or "").strip()).rstrip("`").strip()
    try:
        data = json.loads(txt)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if not any(data.get(k) for k in ("goal", "decisions", "steps", "outcome")):
        return None
    return data


_TOOL_MARKERS = (
    "[工具结果", "Tool Results", "tool_call", "tool_result", "stdout", "stderr",
    "=== 工具调用结果 ===", "[自动验证]", "Command failed", "returncode",
)


def _is_tool_line(text: str) -> bool:
    low = text.lower()
    return any(m.lower() in low for m in _TOOL_MARKERS)


def _conversation_digest(workspace_dir: str, chat_id: str, limit: int = 6) -> str:
    """Conversation-only digest (tool-marked lines dropped) for distillation."""
    try:
        from research_agent.memory import get_recent_turns
        turns = get_recent_turns(workspace_dir, chat_id, limit=limit)
    except Exception:
        return ""
    lines: list[str] = []
    for t in turns:
        user = (getattr(t, "user_message", "") or "").strip()
        assistant = (getattr(t, "assistant_message", "") or "").strip()
        if user:
            lines.append(f"用户: {user}")
        if assistant:
            kept = [ln.strip() for ln in assistant.splitlines()
                    if ln.strip() and not _is_tool_line(ln)]
            if kept:
                lines.append("助手: " + "\n".join(kept))
    return "\n".join(lines)


def build_episode_digest(*, user_input: str = "", final_response: str = "",
                         tool_path: list[str] | None = None,
                         workspace_dir: str = "", chat_id: str = "") -> dict:
    """A compact run digest for episode distillation (tool-name only, no outputs)."""
    return {
        "user_input": user_input or "",
        "final_response": (final_response or "")[:1500],
        "tool_path": list(tool_path or []),
        "conversation": _conversation_digest(workspace_dir, chat_id),
    }


def distill_episode(llm, digest: dict, *, workspace_dir: str = "",
                    chat_id: str = "") -> Plan | None:
    """Small-model episode distillation. Returns None on any failure (caller falls back)."""
    if llm is None:
        return None
    try:
        prompt = EPISODE_EXTRACT_PROMPT.format(
            user=(digest.get("user_input") or "")[:800],
            response=(digest.get("final_response") or "")[:1500],
            tools=", ".join(digest.get("tool_path") or []),
            conversation=(digest.get("conversation") or "")[:3000],
        )
        raw = llm.complete([{"role": "user", "content": prompt}], max_tokens=500,
                           purpose="extract")
    except Exception:
        return None
    data = _parse_episode_json(raw)
    if not data:
        return None

    steps: list[PlanStep] = []
    for s in (data.get("steps") or []):
        if isinstance(s, dict) and str(s.get("step", "")).strip():
            st = str(s.get("status", "done")).strip().lower()
            if st not in STATUSES:
                st = "done"
            steps.append(PlanStep(step=str(s["step"]).strip(), status=st,
                                  result=str(s.get("result", ""))[:300]))
        elif isinstance(s, str) and s.strip():
            steps.append(PlanStep(step=s.strip(), status="done"))

    decisions: list[str] = []
    for d in (data.get("decisions") or []):
        if isinstance(d, dict):
            dec = str(d.get("decision", "")).strip()
            reason = str(d.get("reason", "")).strip()
            if dec:
                decisions.append(f"{dec} — {reason}" if reason else dec)
        elif isinstance(d, str) and d.strip():
            decisions.append(d.strip())

    goal = str(data.get("goal", "")).strip()
    outcome = str(data.get("outcome", "")).strip()
    if not (goal or steps or decisions or outcome):
        return None
    return Plan(
        goal=goal[:200],
        steps=steps,
        decisions=decisions,
        outcome=outcome[:300],
        tools_used=list(dict.fromkeys(digest.get("tool_path") or [])),
        workspace_dir=workspace_dir,
        chat_id=chat_id,
    )


_TOKEN = re.compile(r"[\w\u4e00-\u9fff]+")
_JIEBA_QUIET = False


def _tokens(text: str) -> set[str]:
    """Tokenize for keyword recall (jieba for CJK, English by regex)."""
    global _JIEBA_QUIET
    text = text or ""
    out: set[str] = set()
    if any("\u4e00" <= c <= "\u9fff" for c in text):
        try:
            import jieba
            if not _JIEBA_QUIET:
                import logging as _logging
                jieba.setLogLevel(_logging.WARNING)   # silence "Building prefix dict…"
                _JIEBA_QUIET = True
            out.update(t.strip().lower() for t in jieba.cut(text))
        except Exception:
            out.update(_TOKEN.findall(text.lower()))
    out.update(re.findall(r"[a-zA-Z0-9]+", text.lower()))
    return {t for t in out if len(t) > 1}


def _score(query: str, recs: list[dict]) -> list[tuple[dict, float]]:
    """[(record, score)] — vector-first (numpy cosine) with jieba-keyword fallback."""
    try:
        from research_agent import embedding as _emb
        if _emb.is_available():
            qv = _emb.encode_query(query)
            if qv is not None:
                qv = np.asarray(qv, dtype=np.float32)
                dim = int(qv.shape[0])
                pairs = [(r, r["emb"]) for r in recs
                         if r["emb"] is not None and r["emb"].shape[0] == dim]
                if pairs:
                    M = np.vstack([v for _, v in pairs])   # (N, dim), normalized
                    sims = M @ qv                          # cosine (normalized dot)
                    return [(pairs[i][0], float(sims[i])) for i in range(len(pairs))]
    except Exception:
        pass
    # keyword fallback (no embedding layer)
    q = _tokens(query)
    if not q:
        return []
    out: list[tuple[dict, float]] = []
    for r in recs:
        overlap = len(q & r["tokens"])
        if overlap:
            out.append((r, float(overlap)))
    return out


def search_plans(workspace_dir: str, query: str, limit: int = 3,
                 cross_project: bool = True, prefer_workspace: bool = True) -> list[Plan]:
    """Recall task episodes (agentic: the agent supplies the query).

    Cross-project by default — a same-project task's context is usually already in
    the conversation, so the real value is remembering *other* projects. Vector-first
    (numpy cosine over cached embeddings) with a jieba/keyword fallback;
    `prefer_workspace` adds a small tie-break bonus so same-project episodes lead
    when equally relevant.
    """
    if not query or not query.strip():
        return []
    recs = _load_records()
    if not cross_project:
        ws = os.path.abspath(workspace_dir or "")
        recs = [r for r in recs if os.path.abspath(r["workspace"]) == ws]
    if not recs:
        return []
    scored = _score(query, recs)
    if not scored:
        return []
    if prefer_workspace and workspace_dir:
        ws = os.path.abspath(workspace_dir)
        scored = [(r, s + (0.02 if os.path.abspath(r["workspace"]) == ws else 0.0))
                  for r, s in scored]
    scored.sort(key=lambda x: (x[1], x[0]["created_at"]), reverse=True)
    return [Plan.from_dict(r["plan"]) for r, _ in scored[:limit]]
