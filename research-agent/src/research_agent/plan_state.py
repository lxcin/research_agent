"""Plan capability state declaration, independent of tool registration."""
from research_agent.plan import Plan, PlanStep, STATUSES
from research_agent.state_components import StateComponent


def _validate(value):
    if value is None:
        return
    if not isinstance(value.goal, str) or not isinstance(value.steps, list):
        raise TypeError("Invalid plan goal/steps")
    for step in value.steps:
        if not isinstance(step, PlanStep) or not isinstance(step.step, str) or step.status not in STATUSES:
            raise TypeError("Invalid plan step")


def _encode(value):
    return value.to_dict() if value is not None else None


def _decode(value):
    return Plan.from_dict(value) if value is not None else None


CURRENT_PLAN = StateComponent("plan.current", lambda: None, owner="plan", lifetime="task",
    value_type=(Plan, type(None)), validate=_validate, encode=_encode, decode=_decode, copy_on_read=True)


def current_plan(state):
    """Consume the legacy slot once; subsequent writes have one source of truth."""
    store = state.components
    legacy = state.caps.get("plan")
    if legacy is not None:
        store.inject(CURRENT_PLAN)
        if store.get(CURRENT_PLAN.key) is None:
            store.view("plan").set(CURRENT_PLAN.key, _decode(legacy))
        state.caps.pop("plan", None)
    if CURRENT_PLAN.key not in store.keys():
        return None
    return store.view("plan").get(CURRENT_PLAN.key)


def set_current_plan(state, plan):
    state.components.inject(CURRENT_PLAN)
    state.components.view("plan").set(CURRENT_PLAN.key, plan)
    state.caps.pop("plan", None)
