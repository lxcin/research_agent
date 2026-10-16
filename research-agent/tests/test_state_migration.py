import copy
import threading
import pytest

from research_agent.models import AgentState
from research_agent.plan import Plan, PlanStep
from research_agent.plan_state import CURRENT_PLAN, current_plan, set_current_plan
from research_agent import sandbox
from research_agent.tools import ToolRegistry
from research_agent.tools.builtin import _plugins
from research_agent.tools.schema import ToolResult


def test_plan_legacy_input_is_consumed_and_reads_are_detached():
    state = AgentState()
    state.caps["plan"] = Plan(goal="original", steps=[PlanStep("first")]).to_dict()
    first = current_plan(state)
    first.steps[0].status = "done"
    assert current_plan(state).steps[0].status == "pending"
    assert "plan" not in state.caps
    assert state.components._specs[CURRENT_PLAN.key].owner == "plan"


def test_session_snapshot_does_not_carry_task_state(tmp_path):
    state = AgentState(constraints=["keep user edits"])
    set_current_plan(state, Plan(goal="old task"))
    path = tmp_path / "session.json"
    state.components.save(path, lifetimes={"session"})
    restored = AgentState()
    restored.components.inject(CURRENT_PLAN)
    restored.components.restore(path, lifetimes={"session"})
    assert restored.constraints == ["keep user edits"]
    assert current_plan(restored) is None


def test_completed_call_replays_declared_fields_without_repeating_effect():
    from research_agent.execution import execute_once
    state = AgentState()
    state.components.inject(CURRENT_PLAN)
    calls = []
    def invoke():
        calls.append(1)
        set_current_plan(state, Plan(goal="recover", steps=[PlanStep("first", status="done")]))
        return ToolResult.ok()
    execute_once("task", "call", "plan_update", {}, invoke, state=state)
    set_current_plan(state, None)
    execute_once("task", "call", "plan_update", {}, invoke, state=state)
    assert calls == [1]
    assert current_plan(state).steps[0].status == "done"


def test_clone_isolates_data_resources_and_pending_approvals(tmp_path):
    parent = AgentState(constraints=["parent constraint"])
    parent._pending_confirms["pending"] = {"event": threading.Event()}
    set_current_plan(parent, Plan(goal="parent"))
    session = sandbox.get_session(parent, str(tmp_path), sandbox.SandboxConfig(backend="local"))
    child = copy.copy(parent)
    assert child.components.get("shell.session") is None
    assert child._pending_confirms == {}
    set_current_plan(child, Plan(goal="child"))
    child.components.view("core").update("constraints", lambda value: value + ["child only"])
    assert current_plan(parent).goal == "parent"
    assert parent.constraints == ["parent constraint"]
    assert parent.components.get("shell.session") is session
    assert "shell.session" not in parent.components.snapshot()
    sandbox.end_run(parent)


def test_shell_disable_cleans_bound_resources_after_drain(tmp_path):
    registry = ToolRegistry()
    registry.install_plugin(next(p for p in _plugins() if p.id == "shell"), apply_enabled=False)
    state = AgentState()
    registry.bind_state(state)
    session = sandbox.get_session(state, str(tmp_path), sandbox.SandboxConfig(backend="local"))
    registry.disable_plugin("shell", persist=False)
    assert session._closed
    assert "shell.session" not in state.components.keys()
    registry.enable_plugin("shell", persist=False)
    assert state.components.get("shell.session") is None


def test_subagent_cleans_its_resource_on_failure(monkeypatch, tmp_path):
    from research_agent.tools import subagent
    created = []
    parent = AgentState()
    def failing(task, llm, state, *args, **kwargs):
        created.append(sandbox.get_session(state, str(tmp_path), sandbox.SandboxConfig(backend="local")))
        raise RuntimeError("failed child")
    monkeypatch.setattr(subagent, "_run_single_subagent_impl", failing)
    with pytest.raises(RuntimeError, match="failed child"):
        subagent._run_single_subagent({"task": "test"}, None, parent)
    assert created[0]._closed
    assert "shell.session" not in parent.components.keys()


def test_uncloneable_resource_does_not_fall_back_to_parent():
    from research_agent.state_components import StateComponent
    from research_agent.tools.subagent import _clone_state
    state = AgentState()
    state.components.inject(StateComponent("custom.lock", threading.Lock))
    with pytest.raises(RuntimeError, match="Cannot isolate"):
        _clone_state(state)


def test_host_resets_plugin_request_and_task_fields_but_keeps_session():
    from research_agent.state_components import StateComponent
    state = AgentState(user_input="new input")
    for key, lifetime in (("plugin.request", "request"), ("plugin.task", "task"), ("plugin.session", "session")):
        state.components.inject(StateComponent(key, list, owner="plugin", lifetime=lifetime))
        state.components.set(key, ["old"])
    state.components.reset(lifetimes={"request", "task"}, exclude_owners={"core"})
    assert state.components.get("plugin.request") == []
    assert state.components.get("plugin.task") == []
    assert state.components.get("plugin.session") == ["old"]
    assert state.user_input == "new input"


def test_runtime_checkpoint_restores_plan_without_serializing_live_resource(tmp_path):
    from research_agent.runtime import RuntimeContext, RuntimeHost
    state = AgentState()
    state._execution_id = "runtime-state"
    set_current_plan(state, Plan(goal="resume task"))
    session = sandbox.get_session(state, str(tmp_path), sandbox.SandboxConfig(backend="local"))
    host = RuntimeHost(RuntimeContext(llm=None, user_input="task", state=state))
    host.checkpoint({"messages": [], "rounds": 1})
    set_current_plan(state, None)
    restored = host.checkpoint()
    assert restored["rounds"] == 1
    assert current_plan(state).goal == "resume task"
    assert state.components.get("shell.session") is session
    assert "shell.session" not in restored["fields"]
    sandbox.end_run(state)
