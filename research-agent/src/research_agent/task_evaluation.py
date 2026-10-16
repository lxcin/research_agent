"""Explicit, isolated task benchmarks. No model calls occur on registration."""
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from statistics import mean
import tempfile
import time
from typing import Callable

from research_agent.evaluation import EvaluationInput, EvaluationSpec, EvaluationResult


def workspace_path(root, name):
    """Fixture and oracle paths must remain inside this trial's workspace."""
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts or ":" in normalized or path == PurePosixPath("."):
        raise ValueError(f"Invalid benchmark path: {name}")
    root = Path(root).resolve()
    target = (root / normalized).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"Benchmark path escapes workspace: {name}")
    return target


@dataclass(frozen=True)
class TaskCase:
    id: str
    prompt: str
    checks: tuple[EvaluationSpec, ...]
    files: dict[str, str] = field(default_factory=dict)
    reference: dict = field(default_factory=dict)
    constraints: tuple[str, ...] = ()
    max_rounds: int = 12
    version: str = "1"

    def __post_init__(self):
        if not self.id or not self.prompt or not self.version or not self.checks:
            raise ValueError("Task case requires identity, prompt, version and checks")
        if isinstance(self.max_rounds, bool) or not isinstance(self.max_rounds, int) or self.max_rounds < 1:
            raise ValueError("Task max_rounds must be positive")
        if any(check.dimension not in ("task", "strategy") for check in self.checks):
            raise ValueError("Task case checks must evaluate task or strategy")
        if not any(check.dimension == "task" for check in self.checks):
            raise ValueError("Task case needs at least one task correctness check")
        if len({check.id for check in self.checks}) != len(self.checks):
            raise ValueError("Duplicate task check id")
        for name, value in self.files.items():
            workspace_path(Path.cwd(), name)
            if not isinstance(value, str):
                raise TypeError("Fixtures must be text")
        json.dumps(self.reference, allow_nan=False)

    def fingerprint(self):
        payload = [self.id, self.version, self.prompt, self.files, self.reference,
                   self.constraints, self.max_rounds,
                   [(c.id, c.version, c.target, c.dimension) for c in self.checks]]
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class RegisteredCase:
    owner: str
    case: TaskCase
    lease: Callable | None = field(default=None, repr=False, compare=False)

    @property
    def key(self):
        return self.owner + "/" + self.case.id


@dataclass(frozen=True)
class TaskExecution:
    response: str
    termination_reason: str = "completed"
    rounds: int = 0
    events: tuple[dict, ...] = ()
    # Unknown/missing measurements stay unknown, never zero-filled.
    metrics: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.response, str) or not isinstance(self.termination_reason, str):
            raise TypeError("Invalid task execution response/termination")
        if isinstance(self.rounds, bool) or not isinstance(self.rounds, int) or self.rounds < 0:
            raise ValueError("Invalid task rounds")
        for value in self.metrics.values():
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                raise ValueError("Metrics must be nonnegative finite numbers or None")


@dataclass(frozen=True)
class TaskRequest:
    """Runner input deliberately excludes references and oracle callbacks."""
    id: str
    prompt: str
    constraints: tuple[str, ...]
    max_rounds: int
    version: str


@dataclass(frozen=True)
class StrategySpec:
    id: str
    run: Callable[[TaskRequest, Path], TaskExecution]
    version: str = "1"
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.id or not self.version or not callable(self.run):
            raise ValueError("Invalid benchmark strategy")
        json.dumps(self.metadata, allow_nan=False)


def _percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    lo, hi = math.floor(position), math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def _summarize(rows):
    counts = {key: sum(row["status"] == key for row in rows) for key in ("pass", "fail", "skip", "error")}
    metrics = {}
    for key in ("wall_ms", "rounds", "model_calls", "prompt_tokens", "completion_tokens", "cost_usd"):
        values = [row["metrics"][key] for row in rows if row["metrics"].get(key) is not None]
        metrics[key] = {"observed": len(values), "missing": len(rows) - len(values),
                        "mean": mean(values) if values else None,
                        "p50": _percentile(values, .5), "p95": _percentile(values, .95)}
    return {"total": len(rows), **counts, "pass_rate": counts["pass"] / len(rows) if rows else None,
            "completed": sum(row["termination_reason"] == "completed" for row in rows), "metrics": metrics}


def run_benchmark(cases, strategies, *, repeats=1, workspace_root=None):
    """Run every case/strategy in a fresh directory, then compare paired trials."""
    from research_agent.execution import RecoveryRequired, Cancelled
    from research_agent.context_budget import ContextBudgetExceeded
    from research_agent.model_retry import ModelRequestRejected, PartialResponseError
    cases = tuple(RegisteredCase(entry.owner, deepcopy(entry.case), entry.lease) for entry in cases)
    strategies = tuple(strategies)
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1 or not cases or not strategies:
        raise ValueError("Benchmark needs cases, strategies and positive repeats")
    if len({c.key for c in cases}) != len(cases) or len({s.id for s in strategies}) != len(strategies):
        raise ValueError("Duplicate case or strategy identity")
    rows = []
    # Alternate order to reduce systematic first-run/cache effects. This is not randomization.
    for trial in range(repeats):
        ordered = strategies if trial % 2 == 0 else tuple(reversed(strategies))
        for entry in cases:
            # Hold the owning plugin through execution and oracle checks, not just listing.
            for strategy in ordered:
                row = {"case": entry.key, "case_version": entry.case.version,
                       "fingerprint": entry.case.fingerprint(), "trial": trial,
                       "strategy": strategy.id, "status": "error", "checks": [],
                       "termination_reason": "error", "response": "", "metrics": {}}
                started = time.monotonic()
                try:
                    with entry.lease() if entry.lease is not None else nullcontext():
                        with tempfile.TemporaryDirectory(prefix="agent-eval-", dir=workspace_root) as folder:
                            workspace = Path(folder)
                            case = deepcopy(entry.case)
                            for name, text in case.files.items():
                                target = workspace_path(workspace, name)
                                target.parent.mkdir(parents=True, exist_ok=True)
                                target.write_bytes(text.encode("utf-8"))
                            # Reference answers/checks are evaluator-only, never runner/model input.
                            candidate = TaskRequest(case.id, case.prompt, case.constraints, case.max_rounds, case.version)
                            result = strategy.run(candidate, workspace)
                            if not isinstance(result, TaskExecution):
                                raise TypeError("Strategy must return TaskExecution")
                            row.update(response=result.response, termination_reason=result.termination_reason)
                            row["metrics"] = dict(result.metrics, rounds=result.rounds,
                                                  wall_ms=(time.monotonic() - started) * 1000)
                            data = EvaluationInput(result.events, {"workspace": str(workspace),
                                "response": result.response, "reference": deepcopy(case.reference),
                                "termination_reason": result.termination_reason, "metrics": dict(row["metrics"])})
                            for spec in case.checks:
                                try:
                                    verdict = spec.check(deepcopy(data))
                                    if not isinstance(verdict, EvaluationResult):
                                        raise TypeError("Task checker must return EvaluationResult")
                                except Exception as exc:
                                    verdict = EvaluationResult("error", str(exc))
                                row["checks"].append({"id": spec.id, "dimension": spec.dimension,
                                                      "status": verdict.status, "detail": verdict.detail})
                            statuses = {item["status"] for item in row["checks"]}
                            row["status"] = ("error" if "error" in statuses else "fail" if
                                "fail" in statuses or result.termination_reason != "completed" else
                                "skip" if "skip" in statuses else "pass")
                except (Exception, RecoveryRequired, Cancelled, ContextBudgetExceeded, ModelRequestRejected, PartialResponseError) as exc:
                    row["status"] = "error"
                    row["error"] = f"{type(exc).__name__}: {exc}"
                    row["metrics"]["wall_ms"] = (time.monotonic() - started) * 1000
                rows.append(row)
    summaries = {s.id: _summarize([r for r in rows if r["strategy"] == s.id]) for s in strategies}
    baseline = strategies[0].id
    comparisons = []
    reference = {(r["case"], r["trial"]): r for r in rows if r["strategy"] == baseline}
    for strategy in strategies[1:]:
        pairs = [(reference[(r["case"], r["trial"])], r) for r in rows if r["strategy"] == strategy.id]
        deltas = {}
        for key in ("wall_ms", "rounds", "model_calls", "prompt_tokens", "completion_tokens", "cost_usd"):
            known = [(a["metrics"][key], b["metrics"][key]) for a, b in pairs
                     if a["metrics"].get(key) is not None and b["metrics"].get(key) is not None]
            deltas[key] = {"paired": len(known), "mean_delta": mean(b - a for a, b in known) if known else None}
        comparisons.append({"baseline": baseline, "candidate": strategy.id, "paired": len(pairs),
            "wins": sum(a["status"] != "pass" and b["status"] == "pass" for a, b in pairs),
            "losses": sum(a["status"] == "pass" and b["status"] != "pass" for a, b in pairs),
            "pass_rate_delta": summaries[strategy.id]["pass_rate"] - summaries[baseline]["pass_rate"],
            "metrics": deltas})
    import platform
    return {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "environment": {"python": platform.python_version(), "platform": platform.system()},
            "repeats": repeats, "strategies": [{"id": s.id, "version": s.version, "metadata": s.metadata} for s in strategies],
            "summary": summaries, "comparisons": comparisons, "runs": rows}


def write_benchmark_report(report, path):
    """Atomic JSON and Markdown reports. No model calls, no implicit test launch."""
    import os
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Agent 任务评测", "", "| 策略 | 通过/总数 | 失败 | 跳过 | 错误 | 通过率 |", "|---|---|---|---|---|---|"]
    for name, summary in report["summary"].items():
        label = name.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {label} | {summary['pass']}/{summary['total']} | {summary['fail']} | {summary['skip']} | {summary['error']} | {summary['pass_rate']:.1%} |")
    lines += ["", "| 策略 | 耗时 P50 / P95（ms） | 平均模型调用数 | 平均输入 token | 平均费用 USD（有数据/总数） |",
              "|---|---|---|---|---|"]
    def number(value):
        return "未知" if value is None else f"{value:.6g}"
    for name, summary in report["summary"].items():
        metrics = summary["metrics"]
        label = name.replace("|", "\\|").replace("\n", " ")
        wall, cost = metrics["wall_ms"], metrics["cost_usd"]
        lines.append(f"| {label} | {number(wall['p50'])} / {number(wall['p95'])} | {number(metrics['model_calls']['mean'])} | {number(metrics['prompt_tokens']['mean'])} | {number(cost['mean'])}（{cost['observed']}/{summary['total']}） |")
    lines += ["", "| 基线 → 候选 | 配对数 | 胜 / 负（按任务通过） | 通过率差值 |", "|---|---|---|---|"]
    for comparison in report["comparisons"]:
        names = (comparison["baseline"] + " → " + comparison["candidate"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {names} | {comparison['paired']} | {comparison['wins']} / {comparison['losses']} | {comparison['pass_rate_delta']:+.1%} |")
    lines += ["", "完成退出不代表任务正确；跳过和错误不算通过。费用/用量缺失不补零。",
              "配对差值为候选减基线；小样本与脚本模型结果不能证明真实模型收益。", "", "详细检查、版本、用量覆盖率和配对差值见 JSON 报告。"]
    outputs = (path.with_suffix(".json"), path.with_suffix(".md"))
    for target, text in zip(outputs, (json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), "\n".join(lines) + "\n")):
        descriptor, temporary = tempfile.mkstemp(dir=target.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return outputs
