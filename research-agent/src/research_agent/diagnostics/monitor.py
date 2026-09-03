"""RunMonitor — deterministic fault detection over the diagnostic event stream.

Watches events emitted during one agent run and produces structured `fault`
records for convergence / stability problems. Pure rules — no LLM — so every
rule is unit-testable by feeding synthetic events.

Fault categories produced (kind):
  - empty_streak        retrieve/search returned nothing repeatedly
  - tool_loop           same tool + near-identical params called repeatedly
  - error_streak        same tool failing back-to-back
  - search_exhausted    search call limit reached
  - llm_unstable        repeated LLM call retries/failures
  - no_response         run ended without a usable final reply
"""
import re
from collections import Counter

# Tool events that represent "search returned nothing".
EMPTY_STATUS = {"local_empty", "empty", "no_results", "found_0"}
# Thinking-message keywords that indicate degraded convergence.
LLM_UNSTABLE_TEXT = ["LLM 重试", "模型调用失败", "重试次数已达上限"]
SEARCH_EXHAUST_TEXT = ["检索次数已达上限", "搜索次数已达上限"]

_DEFAULT_TOOL_LOOP_LIMIT = 4
_DEFAULT_EMPTY_STREAK_LIMIT = 3
_DEFAULT_ERROR_STREAK_LIMIT = 3


class RunMonitor:
    def __init__(self, tool_loop_limit=_DEFAULT_TOOL_LOOP_LIMIT,
                 empty_streak_limit=_DEFAULT_EMPTY_STREAK_LIMIT,
                 error_streak_limit=_DEFAULT_ERROR_STREAK_LIMIT):
        self.tool_loop_limit = tool_loop_limit
        self.empty_streak_limit = empty_streak_limit
        self.error_streak_limit = error_streak_limit

        # sliding state
        self._last_tool = None
        self._last_params_sig = None
        self._tool_loop_count = 0
        self._empty_streak = 0
        self._last_error_tool = None
        self._error_streak = 0
        self._tool_calls: Counter = Counter()
        self._faults: list[dict] = []
        self._search_calls = 0

    # ── helpers ──

    def _params_signature(self, data: dict) -> str:
        return str(data.get("input") or data.get("params") or "")

    def _add_fault(self, kind: str, detail: dict):
        detail = dict(detail)
        detail["kind"] = kind
        detail["tool"] = detail.get("tool", self._last_tool or "")
        self._faults.append(detail)

    # ── observation ──

    def observe(self, event_type: str, data: dict):
        """Feed one event; may append faults. Call at run start with type='start'."""
        if event_type == "start":
            self.reset()
            return
        if event_type == "tool_start":
            self._on_tool_start(data)
        elif event_type == "tool_end":
            self._on_tool_end(data)
        elif event_type in ("tool",):
            # tool-level status events (start/done/local_empty/...)
            status = str(data.get("status", ""))
            tool = data.get("tool", "")
            if status in EMPTY_STATUS or data.get("found") == 0:
                self._on_empty(tool)
            if status in ("error", "failed"):
                self._on_tool_error(tool)
        elif event_type == "thinking":
            text = str(data.get("text", ""))
            self._scan_thinking(text)
        elif event_type == "search_call":
            self._search_calls += 1

    def _on_tool_start(self, data: dict):
        name = data.get("name", "")
        sig = self._params_signature(data)
        self._tool_calls[name] += 1
        if name in ("retrieve", "search_papers"):
            self._search_calls += 1
        if name == self._last_tool and sig == self._last_params_sig:
            self._tool_loop_count += 1
            if self._tool_loop_count >= self.tool_loop_limit:
                self._add_fault("tool_loop", {"tool": name, "repeats": self._tool_loop_count})
                self._tool_loop_count = 0
        else:
            self._last_tool = name
            self._last_params_sig = sig
            self._tool_loop_count = 1

    def _on_tool_end(self, data: dict):
        status = data.get("status", "")
        tool = data.get("name", data.get("tool", ""))
        if status == "success":
            self._error_streak = 0
            self._last_error_tool = None
        elif status in ("error", "failed"):
            self._on_tool_error(tool)

    def _on_empty(self, tool: str):
        self._empty_streak += 1
        if self._empty_streak >= self.empty_streak_limit:
            self._add_fault("empty_streak", {"streak": self._empty_streak, "tool": tool})
            self._empty_streak = 0

    def _on_tool_error(self, tool: str):
        if tool == self._last_error_tool:
            self._error_streak += 1
        else:
            self._last_error_tool = tool
            self._error_streak = 1
        if self._error_streak >= self.error_streak_limit:
            self._add_fault("error_streak", {"streak": self._error_streak, "tool": tool})
            self._error_streak = 0

    def _scan_thinking(self, text: str):
        if any(k in text for k in SEARCH_EXHAUST_TEXT):
            self._add_fault("search_exhausted", {"hint": text[:120]})
        elif any(k in text for k in LLM_UNSTABLE_TEXT):
            self._add_fault("llm_unstable", {"hint": text[:120]})

    # ── lifecycle ──

    def reset(self):
        self._last_tool = None
        self._last_params_sig = None
        self._tool_loop_count = 0
        self._empty_streak = 0
        self._last_error_tool = None
        self._error_streak = 0
        self._tool_calls = Counter()
        self._faults = []
        self._search_calls = 0

    def finalize(self, final_response: str = ""):
        """Call at run end. Emits no_response when nothing usable was produced."""
        resp = (final_response or "").strip()
        has_reply = len(resp) > 0 and not resp.isspace()
        if not has_reply:
            self._add_fault("no_response", {"hint": "run ended with empty final reply"})
        return self._faults

    @property
    def faults(self) -> list[dict]:
        return list(self._faults)

    @property
    def tool_calls(self) -> dict:
        return dict(self._tool_calls)

    @property
    def search_calls(self) -> int:
        return self._search_calls


def _fault_text(fault: dict) -> str:
    """Human-readable summary of a fault record."""
    k = fault.get("kind", "")
    t = fault.get("tool", "")
    if k == "empty_streak":
        return f"连续 {fault.get('streak')} 次检索返回空"
    if k == "tool_loop":
        return f"工具 {t} 重复调用 {fault.get('repeats')} 次（相同参数）"
    if k == "error_streak":
        return f"工具 {t} 连续失败 {fault.get('streak')} 次"
    if k == "search_exhausted":
        return f"搜索次数耗尽: {fault.get('hint', '')}"
    if k == "llm_unstable":
        return f"LLM 不稳定: {fault.get('hint', '')}"
    if k == "no_response":
        return "会话结束但未生成可用回复"
    return f"{k} {fault}"
