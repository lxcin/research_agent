from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
import subprocess
import pytest

from research_agent.models import AgentState
from research_agent.state_components import StateComponent
from research_agent.tools import ToolRegistry
from research_agent.tools.schema import ToolPlugin, ToolSchema, ToolResult
from research_agent.execution import RecoveryRequired
from research_agent import sandbox


def data_field(version=1):
    return StateComponent("sample.data", int, owner="sample", lifetime="task",
                          version=version, encode=int, decode=int)


def test_replay_schema_failure_stops_instead_of_returning_tool_failure():
    registry, state, calls = ToolRegistry(), AgentState(), []
    state.components.inject(data_field())
    state._execution_id, state._current_tool_id = "run", "call"
    registry.register(ToolSchema("effect", "single durable effect", {"type": "object"},
                                lambda *a: calls.append(1) or ToolResult.ok()))
    assert registry.dispatch("effect", {}, None, state, None).success
    state.components.remove("sample.data")
    state.components.inject(data_field(version=2))
    with pytest.raises(RecoveryRequired, match="Missing migration"):
        registry.dispatch("effect", {}, None, state, None)
    assert calls == [1]


def test_checkpoint_requires_missing_fields_but_session_loading_is_lenient(tmp_path):
    from research_agent.runtime import RuntimeContext, RuntimeHost
    state = AgentState()
    state.components.inject(data_field())
    state._execution_id = "checkpoint"
    host = RuntimeHost(RuntimeContext(llm=None, user_input="input", state=state))
    host.checkpoint({"messages": []})
    path = tmp_path / "state.json"
    state.components.save(path)
    state.components.remove("sample.data")
    state.constraints = ["current"]
    state.components.restore(path)  # Unloaded plugin data remains legal in a session file.
    state.constraints = ["current"]
    with pytest.raises(RecoveryRequired, match="Unavailable execution state fields"):
        host.checkpoint()
    assert state.constraints == ["current"]


@pytest.mark.parametrize("failure", ["nonzero", "timeout"])
def test_failed_docker_cleanup_preserves_owner_and_can_retry(monkeypatch, tmp_path, failure):
    state = AgentState()
    session = sandbox.get_session(state, str(tmp_path), sandbox.SandboxConfig(backend="local"))
    session.backend, session._container = "docker", "owned-test-container"
    def fail(*a, **k):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("docker", 30)
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(sandbox.subprocess, "run", fail)
    try:
        with pytest.raises((RuntimeError, subprocess.TimeoutExpired)):
            sandbox.end_run(state)
        assert session._container == "owned-test-container" and not session._closed
        assert state.components.get("shell.session") is session
        assert sandbox._FAILED_CLEANUP[id(session)] is session
    finally:
        monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
        sandbox.close_all_sessions()
    assert session._closed and session._container is None
    assert id(session) not in sandbox._FAILED_CLEANUP
    sandbox.end_run(state)
    assert state.components.get("shell.session") is None


def test_binding_during_drain_does_not_deadlock_and_fields_are_removed():
    registry, state = ToolRegistry(), AgentState()
    entered, drain_started, allow_bind = Event(), Event(), Event()
    children = []
    def handler(*args):
        entered.set()
        assert allow_bind.wait(5)
        child = AgentState()
        registry.bind_state(child)  # A draining controller must not hold the table lock.
        children.append(child)
        return ToolResult.ok()
    registry.install_plugin(ToolPlugin(id="sample", state_components=[data_field()], tools=[
        ToolSchema("sample_work", "sample leased worker", {"type": "object"}, handler, plugin_id="sample")
    ]), apply_enabled=False)
    registry.bind_state(state)
    registry.lifecycle.contexts["sample"].scope.on_drain(drain_started.set)
    with ThreadPoolExecutor(max_workers=2) as pool:
        work = pool.submit(registry.dispatch, "sample_work", {}, None, state, None)
        try:
            assert entered.wait(5)
            stop = pool.submit(registry.disable_plugin, "sample", False)
            assert drain_started.wait(5)
            assert not stop.done()
        finally:
            allow_bind.set()
        assert work.result(5).success
        assert stop.result(5)
    assert "sample.data" not in state.components.keys()
    assert "sample.data" not in children[0].components.keys()


def test_parallel_bind_enable_disable_keeps_all_states_consistent():
    registry = ToolRegistry()
    registry.install_plugin(ToolPlugin(id="sample", state_components=[data_field()]), apply_enabled=False)
    states = [AgentState() for _ in range(30)]
    def bind_all():
        for state in states:
            registry.bind_state(state)
    def toggle():
        for _ in range(15):
            registry.disable_plugin("sample", persist=False)
            registry.enable_plugin("sample", persist=False)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(bind_all), pool.submit(toggle), pool.submit(toggle)]
        for future in futures:
            future.result(10)
    registry.disable_plugin("sample", persist=False)
    assert all("sample.data" not in state.components.keys() for state in states)
    registry.enable_plugin("sample", persist=False)
    assert all(state.components.get("sample.data") == 0 for state in states)


def test_default_workspace_resolved_before_admission_and_snapshot_loading(monkeypatch, temp_data_dir):
    from research_agent import agent
    from research_agent.execution import exclusive_request
    from contextlib import contextmanager
    expected = str(temp_data_dir / "workspaces" / "default")
    admissions = []
    @contextmanager
    def admit(workspace, chat):
        admissions.append(workspace)
        with exclusive_request(workspace, chat) as lease:
            yield lease
    def run(user_input, llm, state, on_event, workspace, chat, **kwargs):
        assert workspace == expected
        state.components.restore(agent._component_state_path(workspace, chat), lifetimes={"session"})
        return state
    saved = AgentState(constraints=["persisted"])
    saved.components.save(agent._component_state_path(expected, "chat"), lifetimes={"session"})
    monkeypatch.setattr("research_agent.execution.exclusive_request", admit)
    monkeypatch.setattr(agent, "_run_agent_impl", run)
    state = agent.run_agent("input", None, AgentState(), chat_id="chat")
    assert admissions[0] == expected
    assert state.constraints == ["persisted"]
