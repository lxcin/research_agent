"""Plugin-owned evaluation declarations, independent of model and tool execution."""
from dataclasses import dataclass, field
from typing import Callable, Literal
from threading import RLock
from contextlib import nullcontext


@dataclass(frozen=True)
class EvaluationInput:
    events: tuple[dict, ...] = ()
    artifacts: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationResult:
    status: Literal["pass", "fail", "skip", "error"]
    detail: str = ""

    def __post_init__(self):
        if self.status not in ("pass", "fail", "skip", "error") or not isinstance(self.detail, str):
            raise ValueError("Invalid evaluation result")


@dataclass(frozen=True)
class EvaluationSpec:
    id: str
    target: str
    dimension: Literal["harness", "task", "strategy"]
    check: Callable[[EvaluationInput], EvaluationResult]
    version: str = "1"

    def __post_init__(self):
        if not self.id or not self.version or self.dimension not in ("harness", "task", "strategy") or not callable(self.check):
            raise ValueError("Invalid evaluation declaration")


class EvaluationRegistry:
    def __init__(self):
        self._checks = {}
        self._cases = {}
        self._leases = {}
        self._lock = RLock()

    def register(self, owner: str, spec: EvaluationSpec, lease=None):
        key = (owner, spec.id)
        with self._lock:
            if key in self._checks:
                raise ValueError(f"Duplicate evaluation: {key}")
            self._checks[key] = spec
            self._leases[key] = lease
        def unregister():
            with self._lock:
                if self._checks.get(key) is spec:
                    self._checks.pop(key)
                    self._leases.pop(key, None)
        return unregister

    def register_case(self, owner, case, lease=None):
        from research_agent.task_evaluation import TaskCase, RegisteredCase
        if not isinstance(case, TaskCase):
            raise TypeError("Expected TaskCase")
        key = (owner, case.id)
        entry = RegisteredCase(owner, case, lease)
        with self._lock:
            if key in self._cases:
                raise ValueError(f"Duplicate task case: {key}")
            self._cases[key] = entry
        def unregister():
            with self._lock:
                if self._cases.get(key) is entry:
                    self._cases.pop(key)
        return unregister

    def cases(self):
        with self._lock:
            return tuple(self._cases.values())

    def run(self, data: EvaluationInput):
        results = []
        with self._lock:
            entries = [(key, spec, self._leases[key]) for key, spec in self._checks.items()]
        for (owner, key), spec, lease in entries:
            try:
                with lease() if lease is not None else nullcontext():
                    result = spec.check(data)
                    if not isinstance(result, EvaluationResult):
                        raise TypeError("Evaluator must return EvaluationResult")
            except Exception as exc:
                result = EvaluationResult("error", str(exc))
            results.append({"owner": owner, "id": key, "target": spec.target,
                            "version": spec.version, "dimension": spec.dimension, "status": result.status,
                            "detail": result.detail})
        return results


def lifecycle_checks():
    def evidence_health(data):
        health = data.artifacts.get("evidence_health")
        if health is None:
            return EvaluationResult("skip", "Collector database unavailable")
        if health["pending"] or health["incomplete"]:
            return EvaluationResult("skip", f"Evidence not settled: pending={health['pending']}, incomplete={health['incomplete']}")
        return EvaluationResult("pass" if health["complete"] else "skip", "Evidence ingestion status")
    def session_isolation(data):
        scopes = data.artifacts.get("session_scopes", {})
        if len(scopes) < 2:
            return EvaluationResult("skip", "Need at least two session bindings")
        identities = [scope if isinstance(scope, str) else id(scope) for scope in scopes.values()]
        if len(set(identities)) != len(scopes):
            return EvaluationResult("fail", "Sessions share admission scope")
        return EvaluationResult("pass", "Session admission scopes are independent; not an end-to-end isolation proof")
    def check(kind):
        def evaluate(data):
            traces = data.artifacts.get("resource_scopes")
            if traces is None:
                return EvaluationResult("skip", "No resource lifecycle evidence")
            relevant = False
            for trace in traces:
                draining = False
                active = 0
                for event in trace:
                    if event == "acquire":
                        if draining and kind == "admission":
                            return EvaluationResult("fail", "Call admitted after draining")
                        active += 1
                    elif event == "release":
                        active -= 1
                    elif event == "drain":
                        relevant = True
                        draining = True
                    elif event == "cleanup" and active and kind == "drain":
                        return EvaluationResult("fail", "Resources cleaned while calls active")
                    elif event == "cleanup_error" and kind == "cleanup":
                        return EvaluationResult("fail", "Resource cleanup failed")
            return EvaluationResult("pass" if relevant else "skip", "Lifecycle trace inspected")
        return evaluate
    return tuple(EvaluationSpec(name, "resource_scope", "harness", check(name))
                 for name in ("admission", "drain", "cleanup")) + (
        EvaluationSpec("session_isolation", "session_binding", "harness", session_isolation),
        EvaluationSpec("evidence_health", "collector", "harness", evidence_health))


def telemetry_check(data):
    report = data.artifacts.get("telemetry")
    if not report:
        return EvaluationResult("skip", "No telemetry report")
    return EvaluationResult("pass", "Telemetry available; costs are observations, not task correctness")


def quality_check(data):
    metrics = data.artifacts.get("quality_metrics")
    if metrics is None:
        return EvaluationResult("skip", "No build metrics; evaluation never launches tests implicitly")
    from research_agent.quality.gate import evaluate
    result = evaluate(metrics)
    return EvaluationResult("pass" if result["overall"] == "pass" else "fail", "Build quality gate")
