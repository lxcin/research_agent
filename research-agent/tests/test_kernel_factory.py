import pytest

from research_agent.kernel import AgentKernel, FunctionCallingKernel, KernelResult
from research_agent.kernel_factory import create_kernel, set_default_kernel_factory
from research_agent.runtime import FunctionCallingRuntime


class DirectKernel(AgentKernel):
    def run(self, host, request):
        return KernelResult("factory answer")


def test_factory_constructs_once_and_explicit_kernel_wins():
    constructed = []
    def factory():
        instance = DirectKernel()
        constructed.append(instance)
        return instance
    previous = set_default_kernel_factory(factory)
    try:
        first, second = create_kernel(), create_kernel()
        assert isinstance(first, DirectKernel)
        assert first is second
        assert constructed == [first]
        explicit = FunctionCallingKernel()
        assert create_kernel(explicit) is explicit
        assert FunctionCallingRuntime().kernel is first
    finally:
        set_default_kernel_factory(previous)


def test_invalid_factory_result_does_not_silently_fallback():
    original = create_kernel()
    with pytest.raises(TypeError, match="AgentKernel"):
        set_default_kernel_factory(lambda: None)
    assert create_kernel() is original


def test_switch_preserves_existing_runtime_instance():
    existing = FunctionCallingRuntime()
    original = existing.kernel
    previous = set_default_kernel_factory(DirectKernel)
    try:
        replacement = FunctionCallingRuntime()
        assert replacement.kernel is not existing.kernel
        assert existing.kernel is original
        assert create_kernel() is replacement.kernel
    finally:
        set_default_kernel_factory(previous)


def test_default_factory_reaches_production_entrypoint(temp_workspace, monkeypatch):
    from research_agent.agent import run_agent
    from research_agent.llm import MockLLMProvider
    from research_agent.models import AgentState
    import research_agent.agent as agent

    monkeypatch.setattr(agent, "_maybe_compress", lambda *a: None)
    workspace, chat = temp_workspace
    previous = set_default_kernel_factory(DirectKernel)
    try:
        result = run_agent("hello", MockLLMProvider([]), AgentState(),
                           workspace_dir=workspace, chat_id=chat)
        assert result.final_response == "factory answer"
        assert result.termination_reason == "completed"
    finally:
        set_default_kernel_factory(previous)
