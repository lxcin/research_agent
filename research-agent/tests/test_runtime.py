# tests/test_runtime.py — AgentRuntime contract & replaceable kernel
import pytest

from research_agent.llm import MockLLMProvider
from research_agent.models import AgentState
from research_agent.runtime import (
    RuntimeContext, AgentRuntime, FunctionCallingRuntime,
)


class _EchoRuntime(AgentRuntime):
    """A trivial alternative kernel: answers without any tool loop."""
    id = "echo"

    def run(self, ctx: RuntimeContext) -> AgentState:
        ctx.state.final_response = f"echo:{ctx.user_input}"
        return ctx.state


def test_agent_runtime_is_abstract():
    with pytest.raises(TypeError):
        AgentRuntime()  # noqa


def test_custom_runtime_is_replaceable():
    """Any AgentRuntime impl can drive a request via its contract."""
    state = AgentState(user_input="hello")
    ctx = RuntimeContext(llm=MockLLMProvider(["ignored"]),
                         user_input="hello", state=state)
    result = _EchoRuntime().run(ctx)
    assert result.final_response == "echo:hello"


def test_runtime_registry_style_dispatch():
    """FunctionCallingRuntime can be selected by id (replacement seam)."""
    runtimes = {"function_calling": FunctionCallingRuntime, "echo": _EchoRuntime}
    assert runtimes["echo"]().id == "echo"
    assert runtimes["function_calling"]().id == "function_calling"


def test_function_calling_runtime_streams_on_empty_tools(monkeypatch):
    """Without tools, the kernel streams a plain answer (single LLM call)."""
    llm = MockLLMProvider(["plain answer"])
    state = AgentState(user_input="hi")
    seen = []

    class _NoopRegistry:
        def generate_capabilities(self):
            return ""

        def list_for_llm(self):
            return []

    ctx = RuntimeContext(llm=llm, user_input="hi", state=state,
                         registry=_NoopRegistry(),
                         stream_response=lambda l, m, e: "final",
                         call_llm_with_tools=lambda l, m, t, c="auto": {
                             "content": "answer", "tool_calls": []})
    FunctionCallingRuntime().run(ctx)
    # no tools → immediate final stream, single turn
    assert state.final_response == "final"


def test_runtime_context_defaults():
    ctx = RuntimeContext(llm=MockLLMProvider(["x"]), user_input="u", state=AgentState())
    assert ctx.max_rounds > 0
    assert ctx.registry is None
    assert ctx.emit is None
