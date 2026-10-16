# tests/test_subagent_real.py — real tools/subagent: read-only default, state isolation,
# side-effect serialization. No network (LLM call is monkeypatched).
import threading
import time

from research_agent.models import AgentState
from research_agent.tools import subagent as sub
from research_agent.tools.schema import ToolSchema, ToolResult


def _tool(name, side_effect):
    return ToolSchema(name=name, description=name,
                      parameters={"type": "object", "properties": {}},
                      handler=lambda *a, **k: ToolResult.ok(),
                      plugin_id="test", side_effect=side_effect)


class _Reg:
    def __init__(self, tools, on_dispatch=None):
        self._tools = tools
        self.dispatched = []
        self.on_dispatch = on_dispatch

    @property
    def tools(self):
        return self._tools

    def dispatch(self, name, params, llm, state, emit):
        if self.on_dispatch:
            self.on_dispatch(name)
        self.dispatched.append(name)
        return ToolResult.ok(ok=True)


# ── toolset resolution ──

def test_available_tools_default_readonly():
    tools = {"r": _tool("r", False), "w": _tool("w", True)}
    av = sub._available_tools(tools, None)
    assert "r" in av and "w" not in av


def test_available_tools_explicit_and_empty():
    tools = {"r": _tool("r", False), "w": _tool("w", True)}
    assert "w" in sub._available_tools(tools, ["w"])
    assert sub._available_tools(tools, []) == {}


def test_clone_state_isolates_bookkeeping():
    s = AgentState(user_input="x")
    s._current_tool_id = "parent"
    s._memory_retrievals = ["a"]
    c = sub._clone_state(s)
    assert c is not s
    assert c._current_tool_id == "" and c._memory_retrievals == []
    c._memory_retrievals.append("b")
    assert s._memory_retrievals == ["a"]   # parent untouched


# ── allowlist enforcement in a run ──

def _fake_call_once_tool(name):
    state = {"n": 0}

    def fake(llm, messages):
        state["n"] += 1
        if state["n"] == 1:
            return {"content": "", "tool_calls": [{"id": "1", "name": name, "params": {}}]}
        return {"content": '{"summary": "done"}'}
    return fake


def test_subagent_blocks_write_tool_by_default(monkeypatch):
    reg = _Reg({"read_tool": _tool("read_tool", False), "write_tool": _tool("write_tool", True)})
    monkeypatch.setattr(sub, "_call_llm", _fake_call_once_tool("write_tool"))
    out = sub._run_single_subagent({"task": "t"}, None, AgentState(user_input="x"),
                                   max_rounds=2, registry=reg)
    assert out["summary"] == "done"
    assert reg.dispatched == []          # write tool not in the default (read-only) set


def test_subagent_allows_listed_write_tool(monkeypatch):
    reg = _Reg({"write_tool": _tool("write_tool", True)})
    monkeypatch.setattr(sub, "_call_llm", _fake_call_once_tool("write_tool"))
    sub._run_single_subagent({"task": "t", "tools": ["write_tool"]}, None,
                             AgentState(user_input="x"), max_rounds=2, registry=reg)
    assert reg.dispatched == ["write_tool"]


# ── side-effect serialization across sub-agents ──

def test_spawn_serializes_side_effects(monkeypatch):
    stats = {"cur": 0, "max": 0}
    lk = threading.Lock()

    def on_dispatch(_name):
        with lk:
            stats["cur"] += 1
            stats["max"] = max(stats["max"], stats["cur"])
        time.sleep(0.05)
        with lk:
            stats["cur"] -= 1

    reg = _Reg({"w": _tool("w", True)}, on_dispatch=on_dispatch)

    def fake(llm, messages):
        if any(m.get("role") == "tool" for m in messages):
            return {"content": '{"summary": "done"}'}
        return {"content": "", "tool_calls": [{"id": "1", "name": "w", "params": {}}]}

    monkeypatch.setattr(sub, "_call_llm", fake)
    out = sub.spawn_subagent(
        [{"task": "a", "tools": ["w"]}, {"task": "b", "tools": ["w"]}, {"task": "c", "tools": ["w"]}],
        None, AgentState(user_input="x"), reg, max_rounds=2)
    assert out["completed"] == 3
    assert stats["max"] == 1              # serialized, never concurrent


def test_subagent_respects_max_rounds(monkeypatch):
    reg = _Reg({"r": _tool("r", False)})

    def always_tool(llm, messages):
        return {"content": "", "tool_calls": [{"id": "x", "name": "r", "params": {}}]}

    monkeypatch.setattr(sub, "_call_llm", always_tool)
    out = sub._run_single_subagent({"task": "t"}, None, AgentState(user_input="x"),
                                   max_rounds=3, registry=reg)
    assert out["rounds_used"] <= 3
    assert len(reg.dispatched) == 3          # exactly max_rounds, no runaway


def test_spawn_runs_readonly_concurrently(monkeypatch):
    stats = {"cur": 0, "max": 0}
    lk = threading.Lock()

    def on_dispatch(_):
        with lk:
            stats["cur"] += 1
            stats["max"] = max(stats["max"], stats["cur"])
        time.sleep(0.08)
        with lk:
            stats["cur"] -= 1

    reg = _Reg({"r": _tool("r", False)}, on_dispatch=on_dispatch)

    def fake(llm, messages):
        if any(m.get("role") == "tool" for m in messages):
            return {"content": '{"summary": "done"}'}
        return {"content": "", "tool_calls": [{"id": "1", "name": "r", "params": {}}]}

    monkeypatch.setattr(sub, "_call_llm", fake)
    out = sub.spawn_subagent(
        [{"task": str(i), "tools": ["r"]} for i in range(4)],
        None, AgentState(user_input="x"), reg, max_rounds=2)
    assert out["completed"] == 4
    assert stats["max"] >= 2                  # read-only tools run in parallel (no lock)


def test_spawn_isolates_failure(monkeypatch):
    def fake_run(task, llm, state, max_rounds=3, registry=None, **kw):
        if "bad" in task["task"]:
            raise RuntimeError("boom")
        return {"summary": "ok", "task": task["task"]}

    monkeypatch.setattr(sub, "_run_single_subagent", fake_run)
    out = sub.spawn_subagent(
        [{"task": "good1"}, {"task": "bad x"}, {"task": "good2"}],
        None, AgentState(user_input="x"), None, max_rounds=1)
    assert out["completed"] == 2 and out["failed"] == 1
    assert out["success"] is True


def test_build_subagent_context_bounded():
    ctx = sub._build_subagent_context("summarize X", AgentState(user_input="x"),
                                      parent_context="ref " * 5000, max_tokens=2000)
    assert "summarize X" in ctx
    assert len(ctx) < 10000                   # parent context truncated
    assert sub._build_subagent_context("t", None, "")


def test_merge_summaries():
    class _L:
        def complete(self, messages, **k):
            return "merged"

    assert sub._merge_summaries([{"summary": "a"}, {"summary": "b"}], _L()) == "merged"
    assert sub._merge_summaries([], _L()) == ""
