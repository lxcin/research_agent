# tests/test_loop_guard.py — deterministic loop breaker; feedback rides in tool results.
from research_agent.runtime import _ProgressGuard, _attach_hint, FunctionCallingRuntime, RuntimeContext
from research_agent.models import AgentState
from research_agent.tools.schema import ToolResult


def test_guard_detects_repeated_identical_calls():
    g = _ProgressGuard(loop_limit=3)
    assert g.note_call("shell_exec", {"command": "x"}) is None
    assert g.note_call("shell_exec", {"command": "x"}) is None
    h = g.note_call("shell_exec", {"command": "x"})
    assert h and "重复" in h


def test_guard_resets_on_different_call():
    g = _ProgressGuard(loop_limit=2)
    assert g.note_call("a", {}) is None
    assert g.note_call("b", {}) is None      # different → reset
    assert g.note_call("b", {}) is not None  # b repeated twice


def test_guard_error_streak_and_reset():
    g = _ProgressGuard(error_limit=3)
    assert g.note_result("t", False) is None
    assert g.note_result("t", False) is None
    h = g.note_result("t", False)
    assert h and "连续失败" in h
    assert g.note_result("t", True) is None   # success resets streak


def test_attach_hint():
    assert _attach_hint({"a": 1}, None) == {"a": 1}
    assert _attach_hint({"a": 1}, "x")["_loop_hint"] == "x"


class _Reg:
    def list_for_llm(self):
        return []

    def generate_capabilities(self):
        return ""

    def dispatch(self, name, params, llm, state, emit):
        return ToolResult.ok(ok=True)


def test_runtime_injects_loop_hint_into_tool_result(tmp_path):
    captured = {}
    state = AgentState(user_input="go")

    def call_llm(llm, messages, tools, tool_choice="auto"):
        n = state.round_count
        if n and n <= 5:
            return {"content": "", "tool_calls": [
                {"id": f"c{n}", "name": "noop", "params": {"x": 1}}]}
        return {"content": "done"}

    def stream(llm, messages, emit):
        captured["messages"] = messages
        return "done"

    ctx = RuntimeContext(
        llm=None, user_input="go", state=state, registry=_Reg(),
        workspace_dir=str(tmp_path), chat_id="t",
        emit=lambda *a: None,
        call_llm_with_tools=call_llm, stream_response=stream, max_rounds=8,
    )
    FunctionCallingRuntime().run(ctx)

    tool_msgs = [m for m in captured["messages"] if m.get("role") == "tool"]
    assert tool_msgs, "expected tool result messages"
    assert any("_loop_hint" in m["content"] for m in tool_msgs)
    # no extra system messages were injected by the guard (cache-friendly)
    assert not any(m.get("role") == "system" and "loop_guard" in m.get("content", "")
                   for m in captured["messages"])
