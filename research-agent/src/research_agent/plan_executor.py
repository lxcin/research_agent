"""Plan execution — DAG scheduling over a Plan.

Computes the *ready set* (pending steps whose dependencies are all done), runs
each ready batch in parallel, marks steps done/blocked, and repeats until no
further progress can be made (any remaining pending step becomes blocked = a
cycle or unsatisfiable dependency).

Execution is decoupled from dispatch through a ``dispatch(idx, step) -> dict``
callable, so the scheduler is deterministic and unit-testable with a fake
dispatcher. The `plan_execute` tool wires dispatch to parallel sub-agents
(research_agent.tools.subagent).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from research_agent.plan import Plan, PlanStep

DispatchFn = Callable[[int, PlanStep], dict]


def ready_steps(plan: Plan) -> list[int]:
    """Indices of pending steps whose dependencies (1-based indices) are all done."""
    done = {i for i, s in enumerate(plan.steps) if s.status == "done"}
    ready: list[int] = []
    for i, s in enumerate(plan.steps):
        if s.status != "pending":
            continue
        if all((d - 1) in done for d in s.depends_on):
            ready.append(i)
    return ready


def _run_batch(plan: Plan, indices: list[int], dispatch: DispatchFn,
               max_parallel: int) -> list[tuple[int, dict]]:
    """Run one ready batch, independent steps in parallel."""
    if not indices:
        return []
    if len(indices) == 1:
        i = indices[0]
        return [(i, _safe_dispatch(dispatch, i, plan.steps[i]))]

    out: list[tuple[int, dict]] = []
    workers = max(1, min(max_parallel, len(indices)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_safe_dispatch, dispatch, i, plan.steps[i]): i
                for i in indices}
        for f in as_completed(futs):
            i = futs[f]
            try:
                out.append((i, f.result()))
            except Exception as e:  # pragma: no cover - _safe_dispatch never raises
                out.append((i, {"success": False, "error": str(e)}))
    return out


def _safe_dispatch(dispatch: DispatchFn, idx: int, step: PlanStep) -> dict:
    try:
        res = dispatch(idx, step)
        return res if isinstance(res, dict) else {"success": bool(res)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def execute_plan(plan: Plan, dispatch: DispatchFn, *, max_parallel: int = 6,
                 max_batches: int = 100,
                 on_batch: Callable[[Plan], None] | None = None) -> Plan:
    """Execute a plan in dependency order; returns the mutated plan.

    Independent ready steps run in parallel. A step that fails is marked
    `blocked` (so its dependents are never scheduled). Steps stuck behind a cycle
    or an unsatisfiable dependency end up `blocked` too.
    """
    batches = 0

    while batches < max_batches:
        ready = ready_steps(plan)
        if not ready:
            break
        batches += 1
        results = _run_batch(plan, ready, dispatch, max_parallel)
        for idx, res in results:
            step = plan.steps[idx]
            if res.get("success"):
                step.status = "done"
                step.result = str(res.get("summary", res.get("result", "")))[:500]
            else:
                step.status = "blocked"
                step.result = str(res.get("error", "failed"))[:500]
        if on_batch:
            try:
                on_batch(plan)
            except Exception:
                pass

    # Anything still pending has unsatisfiable deps (cycle / blocked upstream).
    for s in plan.steps:
        if s.status == "pending":
            s.status = "blocked"
            if not s.result:
                s.result = "unresolved dependencies"
    return plan
