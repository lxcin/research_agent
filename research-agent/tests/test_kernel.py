from research_agent.agent import run_agent
from research_agent.kernel import (
    AgentKernel, FunctionCallingKernel, KernelRequest, KernelResult,
    ModelResponse, TerminationReason, ToolCall, ToolObservation,
)
from research_agent.llm import MockLLMProvider
from research_agent.models import AgentState


class ScriptedHost:
    def __init__(self):
        self.calls = []
        self.messages = []

    def prepare(self):
        return [{"role": "user", "content": "test"}]

    def select(self, messages):
        if not self.calls:
            return ModelResponse(tool_calls=(ToolCall("1", "custom", {}),))
        self.messages = messages
        return ModelResponse(content="ready")

    def execute(self, call):
        self.calls.append(call)
        return ToolObservation(True, {"value": 42})

    def after_tool(self, call, messages):
        messages.append({"role": "system", "content": "validated"})

    def answer(self, messages, *, fallback=False):
        return "fallback" if fallback else "answer"

    def emit(self, event_type, data):
        pass

    def set_round(self, number):
        pass


def test_kernel_runs_without_application_dependencies():
    host = ScriptedHost()
    result = FunctionCallingKernel().run(host, KernelRequest("test"))
    assert result == KernelResult("answer", 2)
    assert host.calls[0].name == "custom"
    assert any(m.get("tool_call_id") == "1" for m in host.messages)
    assert host.messages[-1]["content"] == "validated"


def test_kernel_reports_round_limit():
    result = FunctionCallingKernel().run(ScriptedHost(), KernelRequest("test", 1))
    assert result.reason == TerminationReason.ROUND_LIMIT
    assert result.response == "fallback"


def test_shared_kernel_keeps_concurrent_turns_independent():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier = Barrier(2)
    kernel = FunctionCallingKernel()

    class ConcurrentHost(ScriptedHost):
        def prepare(self):
            barrier.wait(timeout=5)
            return super().prepare()

    hosts = [ConcurrentHost(), ConcurrentHost()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda host: kernel.run(host, KernelRequest("test")), hosts))
    assert results == [KernelResult("answer", 2), KernelResult("answer", 2)]
    assert all(len(host.calls) == 1 for host in hosts)
    assert hosts[0].messages is not hosts[1].messages


def test_kernel_reports_model_failure_budget():
    class FailingHost(ScriptedHost):
        def select(self, messages):
            raise RuntimeError("offline")

    result = FunctionCallingKernel().run(FailingHost(), KernelRequest("test", 10))
    assert result.reason == TerminationReason.FAILURE_LIMIT
    assert result.rounds == 5


def test_alternative_kernel_runs_through_production_host(temp_workspace, monkeypatch):
    import research_agent.agent as agent
    workspace, chat = temp_workspace

    class DirectKernel(AgentKernel):
        def run(self, host, request):
            host.emit("reply", {"text": "replacement answer"})
            return KernelResult("replacement answer")

    monkeypatch.setattr(agent, "_call_llm_with_tools", lambda *a: (_ for _ in ()).throw(
        AssertionError("default thinking strategy was invoked")))
    monkeypatch.setattr(agent, "_maybe_compress", lambda *a: None)
    events = []
    state = run_agent("hello", MockLLMProvider([]), AgentState(),
                      on_event=lambda kind, data: events.append(kind),
                      workspace_dir=workspace, chat_id=chat, kernel=DirectKernel())
    assert state.final_response == "replacement answer"
    assert state.termination_reason == "completed"
    assert "reply" in events
    from research_agent.memory import get_recent_turns
    assert get_recent_turns(workspace, chat)[-1].assistant_message == "replacement answer"
