# tests/test_plan_executor.py — DAG scheduling over a Plan
import threading
import time

from research_agent import plan as plan_mod
from research_agent.plan import Plan, PlanStep
from research_agent.plan_executor import execute_plan, ready_steps


def _plan(steps):
    return Plan(goal="g", steps=[PlanStep(**s) for s in steps])


# ── ready_steps (pure) ──

def test_ready_steps_no_deps():
    p = _plan([{"step": "a"}, {"step": "b"}])
    assert ready_steps(p) == [0, 1]


def test_ready_steps_respects_deps():
    p = _plan([{"step": "a"}, {"step": "b", "depends_on": [1]},
               {"step": "c", "depends_on": [2]}])
    assert ready_steps(p) == [0]          # only a
    p.steps[0].status = "done"
    assert ready_steps(p) == [1]          # b now ready
    p.steps[1].status = "done"
    assert ready_steps(p) == [2]


def test_ready_steps_skips_done_and_blocked():
    p = _plan([{"step": "a", "status": "done"}, {"step": "b", "status": "blocked"}])
    assert ready_steps(p) == []


# ── execute_plan ──

def test_execute_sequential_dependency_order():
    p = _plan([{"step": "a"}, {"step": "b", "depends_on": [1]}])
    order = []

    def dispatch(i, step):
        order.append(i)
        return {"success": True, "summary": f"ok{step.step}"}

    execute_plan(p, dispatch, max_parallel=4)
    assert order == [0, 1]                        # b only after a
    assert all(s.status == "done" for s in p.steps)
    assert p.steps[1].result == "okb"


def test_execute_runs_independent_steps_in_parallel():
    p = _plan([{"step": "a"}, {"step": "b"}, {"step": "c"}])
    active = {"n": 0, "max": 0}
    lock = threading.Lock()

    def dispatch(i, step):
        with lock:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
        time.sleep(0.05)
        with lock:
            active["n"] -= 1
        return {"success": True, "summary": "ok"}

    execute_plan(p, dispatch, max_parallel=3)
    assert active["max"] >= 2                      # at least two ran concurrently
    assert all(s.status == "done" for s in p.steps)


def test_execute_failure_blocks_dependents():
    p = _plan([{"step": "a"}, {"step": "b", "depends_on": [1]}])
    called = []

    def dispatch(i, step):
        called.append(i)
        return {"success": False, "error": "boom"}

    execute_plan(p, dispatch)
    assert p.steps[0].status == "blocked"          # step a failed
    assert p.steps[1].status == "blocked"          # dependent never scheduled
    assert called == [0]                            # b never dispatched


def test_execute_cycle_marks_blocked():
    p = _plan([{"step": "a", "depends_on": [2]},
               {"step": "b", "depends_on": [1]}])
    execute_plan(p, lambda i, s: {"success": True})
    assert all(s.status == "blocked" for s in p.steps)


# ── plan_execute tool (dispatch via subagents, monkeypatched) ──

def test_plan_execute_tool_runs_and_updates_state(monkeypatch, tmp_path):
    from research_agent.tools.builtin import plan as plan_tool
    import research_agent.tools.subagent as sub

    calls = []

    def fake_spawn(subtasks, llm, state, registry, max_rounds=3, emit=None):
        calls.append(subtasks[0]["task"])
        return {"success": True, "completed": 1, "failed": 0,
                "summaries": [{"summary": f"done:{subtasks[0]['task']}"}]}

    monkeypatch.setattr(sub, "spawn_subagent", fake_spawn)

    from research_agent.models import AgentState
    st = AgentState(user_input="t")
    st.workspace_dir = str(tmp_path)
    st.caps["plan"] = Plan(goal="g", steps=[
        PlanStep(step="步骤1"),
        PlanStep(step="步骤2", depends_on=[1]),
    ]).to_dict()

    res = plan_tool._handle_plan_execute({}, None, st, lambda e, d: None)
    assert res.success
    assert res.data["done"] == 2 and res.data["blocked"] == 0
    assert calls == ["步骤1", "步骤2"]             # dependency order
    from research_agent.plan_state import current_plan
    assert current_plan(st).steps[1].status == "done"
    assert "plan" not in st.caps


def test_plan_execute_tool_fails_without_plan():
    from research_agent.tools.builtin import plan as plan_tool
    from research_agent.models import AgentState
    st = AgentState(user_input="t")
    assert not plan_tool._handle_plan_execute({}, None, st, lambda e, d: None).success
