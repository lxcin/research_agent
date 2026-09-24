"""Runtime evaluation telemetry (the `telemetry` plugin core).

Collects, for every REAL run: LLM token usage & cost, tool-call path & durations,
phase latency (planning / execution / synthesis), rounds and faults. Emits a
`metrics` event and appends one record to `data_dir/usage/runs.jsonl`, which the
`usage_report` / `usage_query` tools and the evaluation report read.

This is a behavior plugin (like diagnostics): the host calls into it only when
`plugins.telemetry.enabled` is on. No LLM, no network — pure accounting.

Cost is estimated from a local price table (USD per 1M tokens); unknown models
are recorded with cost 0 and flagged. Override via config `telemetry.prices`.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import Counter
from datetime import datetime, timezone

from research_agent.config import get_data_dir, load_config

# USD per 1M tokens — (input, output[, cache_read]). Snapshot; override in
# config.telemetry.prices. cache_read defaults to `input` when omitted (no discount).
_DEFAULT_PRICES = {
    "deepseek-chat": (0.27, 1.10, 0.07),      # cache hit cheaper
    "deepseek-reasoner": (0.55, 2.19, 0.14),
    "deepseek-flash": (0.10, 0.30, 0.02),     # DeepSeek-V4.1-Flash (approx snapshot)
    "deepseek-v4-flash": (0.10, 0.30, 0.02),
    "deepseek-v4-pro": (0.40, 1.60, 0.10),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "claude-3-haiku": (0.25, 1.25),
    "claude-3-5-sonnet": (3.00, 15.00),
}
_PURPOSE_ZH = {"tool_select": "规划/选工具", "answer": "最终答复",
               "extract": "记忆蒸馏", "compress": "上下文压缩", "judge": "评审"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _norm_model(model: str) -> str:
    return (model or "").split("/")[-1].strip().lower()


def _prices() -> dict:
    try:
        cfg = load_config().get("telemetry", {}) or {}
        extra = cfg.get("prices", {}) or {}
    except Exception:
        extra = {}
    table = {k: tuple(v) for k, v in _DEFAULT_PRICES.items()}
    for k, v in extra.items():
        try:
            table[_norm_model(k)] = tuple(float(x) for x in v)   # (in, out[, cache])
        except Exception:
            continue
    return table


def _price_hash(table: dict | None = None) -> str:
    t = table if table is not None else _prices()
    payload = json.dumps({k: list(v) for k, v in sorted(t.items())}, sort_keys=True)
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


def write_price_snapshot() -> str:
    """Persist the price table used for cost estimation (audit trail)."""
    base = os.path.join(str(get_data_dir()), "usage")
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, "prices.json")
    if os.path.isfile(path):
        return path
    table = _prices()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"version": _price_hash(table),
                   "prices_per_1m_usd": {k: {"input": v[0], "output": v[1]}
                                         for k, v in table.items()}},
                  fh, ensure_ascii=False, indent=2)
    return path


def _get(usage, key):
    if usage is None:
        return None
    return usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)


def _tokens(usage) -> tuple[int, int]:
    """Extract (prompt_tokens, completion_tokens) from a litellm usage object/dict."""
    pt = _get(usage, "prompt_tokens") or _get(usage, "input_tokens") or 0
    ct = _get(usage, "completion_tokens") or _get(usage, "output_tokens") or 0
    try:
        return int(pt), int(ct)
    except (TypeError, ValueError):
        return 0, 0


def _cached_tokens(usage) -> int | None:
    """Prompt tokens served from the provider's prompt cache.

    Returns None when the provider did not report cache info (so callers can
    show 'n/a' instead of a misleading 0%).
    """
    details = _get(usage, "prompt_tokens_details")
    if details is not None:
        c = details.get("cached_tokens") if isinstance(details, dict) \
            else getattr(details, "cached_tokens", None)
        if c is not None:
            try:
                return int(c)
            except (TypeError, ValueError):
                pass
    for k in ("prompt_cache_hit_tokens", "cached_tokens", "cache_read_input_tokens"):
        v = _get(usage, k)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                pass
    return None


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int,
                  cached_tokens: int = 0) -> tuple[float, bool]:
    """Return (cost_usd, known). Unknown model → (0.0, False).

    Cache-read tokens are billed at the model's cache price (falls back to input).
    """
    price = _prices().get(_norm_model(model))
    if not price:
        return 0.0, False
    inp, out = price[0], price[1]
    cache_in = price[2] if len(price) > 2 else inp
    cached = max(0, min(int(cached_tokens or 0), int(prompt_tokens)))
    miss = int(prompt_tokens) - cached
    cost = (miss * inp + cached * cache_in + completion_tokens * out) / 1_000_000.0
    return round(cost, 6), True


# ── per-run collector (behavior plugin; host-driven) ─────────────────────────

def _tool_ok(data: dict) -> bool:
    """Tool success by RESULT payload, not just 'didn't raise'.

    Runtime marks `tool_end` status="success" whenever the handler returned a
    ToolResult — even if the underlying command failed (`success=false` /
    non-zero returncode). Classify those as failures.
    """
    if data.get("status") not in ("success", ""):
        return False
    out = data.get("output")
    if isinstance(out, dict):
        if out.get("success") is False:
            return False
        rc = out.get("returncode")
        if isinstance(rc, int) and rc != 0:
            return False
    return True


class _Run:
    def __init__(self, trace: str, workspace: str, chat: str):
        self.trace = trace
        self.workspace = workspace
        self.chat = chat
        self.start = time.monotonic()
        self.llm_calls: list[dict] = []
        self.tools: list[dict] = []
        self._open: dict = {}
        self.faults: Counter = Counter()
        self.path: list[str] = []
        self.completed = False

    def observe(self, event_type: str, data: dict):
        if event_type == "tool_start":
            self._open[data.get("id")] = (data.get("name", ""), time.monotonic())
        elif event_type == "tool_end":
            tid = data.get("id")
            name = data.get("name", "")
            start = self._open.pop(tid, (name, time.monotonic()))[1]
            self.tools.append({"name": name,
                               "duration_ms": round((time.monotonic() - start) * 1000, 1),
                               "status": data.get("status", ""), "ok": _tool_ok(data)})
            self.path.append(name)
        elif event_type == "fault":
            self.faults[data.get("kind", "unknown")] += 1
        elif event_type == "llm_usage":
            self.add_llm(data.get("model", ""), data, data.get("latency_ms", 0.0),
                         data.get("purpose", "answer"))

    def add_llm(self, model: str, usage, latency_ms: float, purpose: str):
        pt, ct = _tokens(usage)
        cached = _cached_tokens(usage)
        cost, known = estimate_cost(model, pt, ct, cached or 0)
        self.llm_calls.append({"model": model, "prompt_tokens": pt, "completion_tokens": ct,
                               "cached_tokens": cached if cached is not None else 0,
                               "cache_reported": cached is not None,
                               "latency_ms": round(latency_ms, 1), "cost_usd": cost,
                               "known_price": known, "purpose": purpose})

    def finalize(self, final_response: str) -> dict:
        self.completed = bool((final_response or "").strip())
        prompt = sum(c["prompt_tokens"] for c in self.llm_calls)
        completion = sum(c["completion_tokens"] for c in self.llm_calls)
        reported = [c["cached_tokens"] for c in self.llm_calls if c.get("cache_reported")]
        cached = sum(reported)
        cache_hit_rate = round(cached / prompt, 4) if (reported and prompt) else None
        cost = round(sum(c["cost_usd"] for c in self.llm_calls), 6)
        unknown = any(not c["known_price"] for c in self.llm_calls)
        by_purpose = Counter(c["purpose"] for c in self.llm_calls)
        rounds = by_purpose.get("tool_select", 0)
        max_prompt = max((c["prompt_tokens"] for c in self.llm_calls), default=0)
        llm_lat = sum(c["latency_ms"] for c in self.llm_calls)
        tool_lat = sum(t["duration_ms"] for t in self.tools)
        plan_llm = sum(c["latency_ms"] for c in self.llm_calls if c["purpose"] == "tool_select")
        answer_llm = sum(c["latency_ms"] for c in self.llm_calls if c["purpose"] == "answer")
        return {
            "trace": self.trace, "workspace": self.workspace, "chat": self.chat,
            "ts": _now_iso(),
            "prices_version": _price_hash(),
            "wall_ms": round((time.monotonic() - self.start) * 1000, 1),
            # "rounds" = model rounds, approximated by tool-selection LLM calls.
            # Prompt tokens are cumulative across rounds (context re-sent each
            # round), so max_prompt_tokens shows context growth; tokens_per_round
            # is the mean cost per round.
            "rounds": rounds,
            "max_prompt_tokens": max_prompt,
            "tokens_per_round": round((prompt + completion) / rounds, 1) if rounds else 0.0,
            "llm_calls": self.llm_calls,
            "tokens": {"prompt": prompt, "completion": completion, "total": prompt + completion},
            "cached_tokens": cached,
            "cache_hit_rate": cache_hit_rate,   # None = provider未上报
            "cost_usd": cost, "cost_known": not unknown,
            "llm_latency_ms": round(llm_lat, 1),
            "tool_latency_ms": round(tool_lat, 1),
            "phases_ms": {"planning": round(plan_llm, 1), "execution": round(tool_lat, 1),
                          "synthesis": round(answer_llm, 1)},
            "tools": self.tools, "path": self.path,
            "tools_ok": sum(1 for t in self.tools if t.get("ok")),
            "tools_total": len(self.tools),
            "faults": dict(self.faults), "completed": self.completed,
        }


_lock = threading.Lock()
_current: _Run | None = None
_last_trace: str = ""          # trace of the most recent run (for post-run attribution)


def active() -> bool:
    """True during a run; also right after one, so post-run LLM calls
    (memory distillation / context compression) can be attributed."""
    return _current is not None or bool(_last_trace)


def begin_run(trace: str = "", workspace: str = "", chat: str = ""):
    global _current
    with _lock:
        _current = _Run(trace, workspace, chat)


def observe(event_type: str, data: dict):
    if _current is not None:
        _current.observe(event_type, data)


def note_llm_call(model: str, usage, latency_ms: float, purpose: str = "tool_select"):
    if _current is not None:
        _current.add_llm(model, usage, latency_ms, purpose)
    elif _last_trace:
        _append_post(_last_trace, model, usage, latency_ms, purpose)


def end_run(final_response: str = "") -> dict | None:
    global _current, _last_trace
    with _lock:
        run = _current
        _current = None
    if run is None:
        return None
    rec = run.finalize(final_response)
    _last_trace = rec.get("trace", "")
    try:
        path = runs_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        write_price_snapshot()   # keep an auditable price snapshot
    except OSError:
        pass
    return rec


# ── aggregation / report / query ─────────────────────────────────────────────

def runs_path() -> str:
    return os.path.join(str(get_data_dir()), "usage", "runs.jsonl")


def posts_path() -> str:
    """Post-run LLM calls (memory distillation / context compression) by trace."""
    return os.path.join(str(get_data_dir()), "usage", "post.jsonl")


def _append_post(trace: str, model: str, usage, latency_ms: float, purpose: str):
    pt, ct = _tokens(usage)
    cached = _cached_tokens(usage)
    cost, _known = estimate_cost(model, pt, ct, cached or 0)
    rec = {"trace": trace, "ts": _now_iso(), "model": model,
           "prompt_tokens": pt, "completion_tokens": ct,
           "cached_tokens": cached if cached is not None else 0,
           "cost_usd": cost, "latency_ms": round(latency_ms, 1), "purpose": purpose}
    try:
        p = posts_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def load_posts() -> list[dict]:
    p = posts_path()
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


def load_runs() -> list[dict]:
    p = runs_path()
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


def query_run(trace: str) -> dict | None:
    for r in reversed(load_runs()):
        if r.get("trace") == trace:
            return r
    return None


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
    return s[idx]


def report(limit: int = 50) -> dict:
    runs = load_runs()[-limit:]
    n = len(runs) or 1
    tokens = [r["tokens"]["total"] for r in runs]
    costs = [r.get("cost_usd", 0.0) for r in runs]
    walls = [r.get("wall_ms", 0.0) for r in runs]
    prompt_sum = sum(r["tokens"]["prompt"] for r in runs)
    cached_sum = sum(r.get("cached_tokens", 0) for r in runs)
    any_cache_reported = any(r.get("cache_hit_rate") is not None for r in runs)
    cache_rate = (round(cached_sum / prompt_sum, 4)
                  if (any_cache_reported and prompt_sum) else None)

    posts = load_posts()
    post_cost = round(sum(p.get("cost_usd", 0.0) for p in posts), 6)
    post_tokens = sum(p.get("prompt_tokens", 0) + p.get("completion_tokens", 0)
                      for p in posts)

    tool_counts: Counter = Counter()
    tool_time: Counter = Counter()
    purpose_cost: Counter = Counter()
    for r in runs:
        for t in r.get("tools", []):
            tool_counts[t["name"]] += 1
            tool_time[t["name"]] += t.get("duration_ms", 0.0)
        for c in r.get("llm_calls", []):
            purpose_cost[c.get("purpose", "?")] += c.get("cost_usd", 0.0)
    for p in posts:
        purpose_cost[p.get("purpose", "?")] += p.get("cost_usd", 0.0)

    return {
        "runs": len(runs),
        "totals": {
            "tokens": sum(tokens) + post_tokens,
            "cost_usd": round(sum(costs) + post_cost, 6),
            "wall_ms": round(sum(walls), 1),
            "cached_tokens": cached_sum,
            "cache_hit_rate": cache_rate,      # None = provider未上报
            "max_prompt_tokens": max((r.get("max_prompt_tokens", 0) for r in runs),
                                     default=0),
            "completed": sum(1 for r in runs if r.get("completed")),
        },
        "post": {"calls": len(posts), "cost_usd": post_cost, "tokens": post_tokens},
        "avg": {"tokens": round(sum(tokens) / n, 1), "cost_usd": round(sum(costs) / n, 6),
                "wall_ms": round(sum(walls) / n, 1)},
        "p95": {"tokens": _pct(tokens, 0.95), "cost_usd": _pct(costs, 0.95),
                "wall_ms": _pct(walls, 0.95)},
        "by_purpose_cost": dict(purpose_cost),
        "top_tools": tool_counts.most_common(10),
        "slow_tools": [{"name": k, "total_ms": round(v, 1)} for k, v in tool_time.most_common(10)],
        "cost_known": all(r.get("cost_known", True) for r in runs),
    }


def render_markdown(rep: dict) -> str:
    t = rep["totals"]
    lines = ["# 运行时评测报告", ""]
    lines.append(f"- 运行数: {rep['runs']} | 完成: {t['completed']}")
    lines.append(f"- 总 token: {t['tokens']} | 总费用: ${t['cost_usd']}"
                 + ("" if rep.get("cost_known") else "（含未知价模型，费用偏低）"))
    if t.get("cache_hit_rate") is None:
        lines.append("- 缓存命中: n/a（provider 未上报 cache token）")
    else:
        lines.append(f"- 缓存命中: {t.get('cached_tokens', 0)} tokens "
                     f"({round(t['cache_hit_rate'] * 100, 1)}% of prompt)")
    post = rep.get("post") or {}
    if post.get("calls"):
        lines.append(f"- 回合后（蒸馏/压缩）: {post['calls']} 次调用 / ${post['cost_usd']} / "
                     f"{post['tokens']} tokens")
    lines.append(f"- 总耗时: {t['wall_ms']} ms | 平均 {rep['avg']['wall_ms']} ms / 次")
    if t.get("max_prompt_tokens"):
        lines.append(f"- 单轮最大 prompt: {t['max_prompt_tokens']} tokens"
                     "（上下文随轮次增长 → 累计 prompt 近似随轮次超线性上升）")
    lines.append("")
    lines.append("## 均值 / P95")
    lines.append(f"- token 平均 {rep['avg']['tokens']} / P95 {rep['p95']['tokens']}")
    lines.append(f"- 费用 平均 ${rep['avg']['cost_usd']} / P95 ${rep['p95']['cost_usd']}")
    lines.append(f"- 耗时 平均 {rep['avg']['wall_ms']} ms / P95 {rep['p95']['wall_ms']} ms")
    lines.append("")
    if rep.get("by_purpose_cost"):
        lines.append("## 费用归因（按阶段）")
        for k, v in sorted(rep["by_purpose_cost"].items(), key=lambda kv: -kv[1]):
            lines.append(f"- {_PURPOSE_ZH.get(k, k)}: ${round(v, 6)}")
        lines.append("")
    if rep.get("top_tools"):
        lines.append("## 工具调用 Top")
        for k, c in rep["top_tools"]:
            lines.append(f"- {k}: {c}")
        lines.append("")
    if rep.get("slow_tools"):
        lines.append("## 最慢工具")
        for s in rep["slow_tools"]:
            lines.append(f"- {s['name']}: {s['total_ms']} ms")
    return "\n".join(lines)


# ── runtime decorator (evaluation as an AgentRuntime, not core-plugin coupling) ─

class MeteredRuntime:
    """Wrap an AgentRuntime to observe usage WITHOUT touching the base loop.

    It decorates the RuntimeContext: wraps `emit` to observe tool/usage events,
    wraps the injected `call_llm_with_tools` primitive to read the additive
    `usage` field, and finalizes the run record at the end. The base runtime and
    the kernel loop are unchanged.
    """

    id = "metered"

    def __init__(self, base):
        self.base = base

    def run(self, ctx):
        begin_run(trace=getattr(ctx, "chat_id", ""),
                  workspace=getattr(ctx, "workspace_dir", ""),
                  chat=getattr(ctx, "chat_id", ""))
        orig_emit = ctx.emit

        def emit(event_type, data):
            try:
                observe(event_type, data)
            except Exception:
                pass
            if orig_emit:
                try:
                    orig_emit(event_type, data)
                except Exception:
                    pass

        ctx.emit = emit

        orig_call = ctx.call_llm_with_tools
        if orig_call is not None:
            def call(llm, messages, tools, tool_choice="auto"):
                t0 = time.monotonic()
                result = orig_call(llm, messages, tools, tool_choice)
                try:
                    model = (result or {}).get("model") or getattr(llm, "model", "")
                    note_llm_call(model, (result or {}).get("usage"),
                                  (time.monotonic() - t0) * 1000, "tool_select")
                except Exception:
                    pass
                return result
            ctx.call_llm_with_tools = call

        try:
            return self.base.run(ctx)
        finally:
            rec = end_run(getattr(ctx.state, "final_response", "") or "")
            if rec and orig_emit:
                try:
                    orig_emit("metrics", {
                        "tokens": rec["tokens"], "cost_usd": rec["cost_usd"],
                        "wall_ms": rec["wall_ms"], "rounds": rec["rounds"],
                        "path": rec["path"], "completed": rec["completed"],
                    })
                except Exception:
                    pass


def write_report(rep: dict | None = None) -> dict:
    rep = rep or report()
    base = os.path.join(str(get_data_dir()), "usage")
    os.makedirs(base, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    md_path = os.path.join(base, f"report-{ts}.md")
    json_path = os.path.join(base, f"report-{ts}.json")
    md = render_markdown(rep)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=2)
    return {"md_path": md_path, "json_path": json_path, "markdown": md}
