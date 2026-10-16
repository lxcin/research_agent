import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from research_agent.evaluation import EvaluationInput, EvaluationSpec, EvaluationResult
from research_agent.evaluation_tasks import filesystem_cases
from research_agent.task_evaluation import (TaskCase, TaskExecution, StrategySpec, RegisteredCase,
    run_benchmark, write_benchmark_report, workspace_path)
from research_agent.tools import ToolRegistry
from research_agent.tools.schema import ToolPlugin, ToolSchema, ToolResult


def simple_case(check=None):
    return TaskCase("simple", "write result.txt", (EvaluationSpec("correct", "artifact", "task",
        check or (lambda data: EvaluationResult("pass" if
            workspace_path(data.artifacts["workspace"], "result.txt").read_text() == "correct" else "fail"))),),
        files={"input.txt": "original"}, reference={"secret": "oracle only"})


def test_cases_are_owned_by_plugins_and_tools_and_follow_lifecycle():
    registry = ToolRegistry()
    case = simple_case()
    tool = ToolSchema("sample", "sample capability", {}, lambda *a: ToolResult.ok(),
                      plugin_id="example", evaluation_cases=[case])
    registry.install_plugin(ToolPlugin("example", tools=[tool], evaluation_cases=[case]), apply_enabled=False)
    assert {entry.key for entry in registry.evaluations.cases()} == {"example/simple", "example/sample/simple"}
    registry.disable_plugin("example", persist=False)
    assert not registry.evaluations.cases()
    registry.enable_plugin("example", persist=False)
    assert len(registry.evaluations.cases()) == 2
    registry.uninstall_plugin("example")
    assert not registry.evaluations.cases()


def test_paired_benchmark_isolates_trials_and_does_not_zero_fill_unknown_usage(tmp_path):
    workspaces = []
    def good(case, workspace):
        assert not hasattr(case, "reference") and not hasattr(case, "checks")
        assert (workspace / "input.txt").read_text() == "original"
        assert not (workspace / "dirty.txt").exists()
        workspaces.append(str(workspace))
        (workspace / "result.txt").write_text("correct")
        (workspace / "dirty.txt").write_text("changed")
        return TaskExecution("done", rounds=2, metrics={"prompt_tokens": 20})
    def bad(case, workspace):
        assert not (workspace / "dirty.txt").exists()
        (workspace / "result.txt").write_text("incorrect")
        return TaskExecution("done", rounds=1)
    report = run_benchmark([RegisteredCase("example", simple_case())],
        [StrategySpec("good", good), StrategySpec("bad", bad)], repeats=2, workspace_root=tmp_path)
    assert report["summary"]["good"]["pass"] == 2
    assert report["summary"]["bad"]["fail"] == 2
    assert report["summary"]["good"]["metrics"]["cost_usd"]["mean"] is None
    assert report["summary"]["bad"]["metrics"]["prompt_tokens"]["missing"] == 2
    assert report["comparisons"][0]["losses"] == 2
    assert report["comparisons"][0]["metrics"]["prompt_tokens"]["paired"] == 0
    assert len(set(workspaces)) == 2 and all(not Path(path).exists() for path in workspaces)
    outputs = write_benchmark_report(report, tmp_path / "reports" / "comparison")
    assert json.loads(outputs[0].read_text(encoding="utf-8"))["schema_version"] == 1
    assert "good" in outputs[1].read_text(encoding="utf-8")


@pytest.mark.parametrize("status,termination,expected", [
    ("skip", "completed", "skip"), ("error", "completed", "error"),
    ("pass", "round_limit", "fail"), ("pass", "completed", "pass")])
def test_completion_missing_evidence_and_errors_are_not_conflated(status, termination, expected):
    case = simple_case(lambda data: EvaluationResult(status))
    report = run_benchmark([RegisteredCase("test", case)],
        [StrategySpec("runner", lambda *a: TaskExecution("answer", termination))])
    assert report["runs"][0]["status"] == expected


def test_runner_exception_does_not_abort_other_strategies():
    from research_agent.execution import RecoveryRequired
    def broken(*a):
        raise RecoveryRequired("unknown outcome")
    case = simple_case(lambda data: EvaluationResult("pass"))
    report = run_benchmark([RegisteredCase("test", case)], [StrategySpec("broken", broken),
        StrategySpec("healthy", lambda *a: TaskExecution("answer"))])
    assert report["summary"]["broken"]["error"] == 1
    assert report["summary"]["healthy"]["pass"] == 1


def test_disabled_case_snapshot_cannot_run_callback():
    registry = ToolRegistry()
    registry.install_plugin(ToolPlugin("example", evaluation_cases=[simple_case()]), apply_enabled=False)
    cases = registry.evaluations.cases()
    registry.disable_plugin("example", persist=False)
    report = run_benchmark(cases, [StrategySpec("never", lambda *a: pytest.fail("must not run"))])
    assert report["summary"]["never"]["error"] == 1


def test_plugin_disable_waits_for_task_execution_and_oracle():
    registry = ToolRegistry()
    entered, release, draining = Event(), Event(), Event()
    def oracle(data):
        entered.set()
        assert release.wait(5)
        return EvaluationResult("pass")
    registry.install_plugin(ToolPlugin("example", evaluation_cases=[simple_case(oracle)]), apply_enabled=False)
    cases = registry.evaluations.cases()
    registry.lifecycle.contexts["example"].scope.on_drain(draining.set)
    with ThreadPoolExecutor(max_workers=2) as pool:
        task = pool.submit(run_benchmark, cases, [StrategySpec("run", lambda *a: TaskExecution("done"))])
        try:
            assert entered.wait(5)
            stop = pool.submit(registry.disable_plugin, "example", False)
            assert draining.wait(5) and not stop.done()
        finally:
            release.set()
        assert task.result(5)["summary"]["run"]["pass"] == 1
        assert stop.result(5)


@pytest.mark.parametrize("path", ["../outside", "C:\\secrets", "/absolute", ".", "a/../../outside"])
def test_fixture_paths_cannot_escape_workspace(path, tmp_path):
    with pytest.raises(ValueError):
        workspace_path(tmp_path, path)


def test_task_oracles_reject_claims_without_artifacts_or_evidence():
    report = run_benchmark([RegisteredCase("filesystem", case) for case in filesystem_cases()],
        [StrategySpec("claims_only", lambda *a: TaskExecution("MISSING"))])
    assert report["summary"]["claims_only"]["pass"] == 0
    assert report["summary"]["claims_only"]["fail"] == 4


class ScriptedAdapter:
    """Transport fixture, not evidence that a real model can solve these tasks."""
    def __init__(self, provider):
        self.count = 0

    def select(self, messages, tools, tool_choice="auto"):
        prompt = next(m["content"] for m in messages if m["role"] == "user")
        if "orders.csv" in prompt:
            actions = [("file_read", {"path": "orders.csv"}),
                       ("file_write", {"expected_version": "absent", "path": "totals.json", "content": '{"east":20,"west":10}'})]
        elif "settings.py" in prompt:
            actions = [("file_read", {"path": "settings.py"}),
                       ("file_edit", {"path": "settings.py", "old_string": "TIMEOUT = 10", "new_string": "TIMEOUT = 30"})]
        elif "missing-report.txt" in prompt:
            actions = [("file_read", {"path": "missing-report.txt"})]
        else:
            actions = [("file_read", {"path": "manifest.json"}), ("file_read", {"path": "data/current.json"}),
                       ("file_write", {"expected_version": "absent", "path": "release.json", "content": '{"release":"v2.4","owner":"lin"}'})]
        idx, self.count = self.count, self.count + 1
        result = {"content": "", "usage": {"prompt_tokens": 100, "completion_tokens": 10}}
        if idx < len(actions):
            name, params = actions[idx]
            if name == "file_edit":
                reads = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
                params["expected_version"] = next(value["version"] for value in reversed(reads) if value.get("path") == params["path"])
            result["tool_calls"] = [{"id": str(idx), "name": name, "params": params}]
        return result

    def answer(self, messages, emit):
        emit("llm_usage", {"prompt_tokens": 20, "completion_tokens": 2})
        return "MISSING" if any("missing-report.txt" in str(m.get("content")) for m in messages) else "done"


def test_all_builtin_tasks_run_through_production_host_with_scripted_transport():
    from research_agent.kernel import FunctionCallingKernel
    from research_agent.evaluation_runtime import runtime_strategy, DirectAnswerKernel
    provider = lambda: SimpleNamespace(model="scripted-fixture")
    agent = runtime_strategy(FunctionCallingKernel(), provider, adapter_factory=ScriptedAdapter,
                             metadata={"model": "scripted-fixture"})
    baseline = runtime_strategy(DirectAnswerKernel(), provider, adapter_factory=ScriptedAdapter)
    report = run_benchmark([RegisteredCase("filesystem", case) for case in filesystem_cases()], [agent, baseline])
    assert report["summary"]["function_calling"]["pass"] == 4, report["runs"]
    assert report["summary"]["direct_answer"]["pass"] == 0
    assert report["summary"]["function_calling"]["metrics"]["prompt_tokens"]["observed"] == 4
    assert report["summary"]["function_calling"]["metrics"]["cost_usd"]["mean"] is None


def test_cli_list_never_calls_model_and_run_reports_failure(monkeypatch, tmp_path):
    from research_agent import cli
    cases = [RegisteredCase("example", simple_case(lambda data: EvaluationResult("fail")))]
    monkeypatch.setattr(cli, "_evaluation_cases", lambda: cases)
    monkeypatch.setattr(cli, "LiteLLMProvider", lambda *a, **k: pytest.fail("must not call model"))
    result = CliRunner().invoke(cli.main, ["evaluation", "list"])
    assert result.exit_code == 0 and "example/simple" in result.output
    from research_agent import evaluation_runtime
    monkeypatch.setattr(evaluation_runtime, "runtime_strategy", lambda kernel, *a, **k:
                        StrategySpec(kernel.id, lambda *a: TaskExecution("wrong")))
    result = CliRunner().invoke(cli.main, ["evaluation", "run", "--strategy", "function_calling",
                                          "--output", str(tmp_path / "report")])
    assert result.exit_code == 1
    assert (tmp_path / "report.json").exists()


def test_checker_mutations_do_not_change_other_checkers_reference():
    def mutate(data):
        data.artifacts["reference"].clear()
        return EvaluationResult("pass")
    case = TaskCase("isolated", "task", (EvaluationSpec("one", "output", "task", mutate),
        EvaluationSpec("two", "output", "task", lambda data: EvaluationResult(
            "pass" if data.artifacts["reference"]["answer"] == 42 else "fail"))), reference={"answer": 42})
    report = run_benchmark([RegisteredCase("test", case)], [StrategySpec("run", lambda *a: TaskExecution("done"))])
    assert report["summary"]["run"]["pass"] == 1


def test_json_oracle_distinguishes_boolean_but_accepts_equal_numeric_values():
    from research_agent.evaluation_tasks import _json_equal
    assert _json_equal({"amount": 20.0}, {"amount": 20})
    assert not _json_equal({"amount": True}, {"amount": 1})
    assert not _json_equal({"amount": 20, "extra": 1}, {"amount": 20})


def test_missing_source_check_requires_matching_tool_call(tmp_path):
    from research_agent.evaluation_tasks import check_missing_evidence
    events = ({"type": "tool_start", "data": {"id": "other", "name": "file_read", "input": {"path": "other.txt"}}},
              {"type": "tool_end", "data": {"id": "other", "name": "file_read", "status": "error"}})
    data = EvaluationInput(events, {"workspace": str(tmp_path), "response": "MISSING",
                                  "reference": {"answer": "MISSING", "missing_path": "missing-report.txt"}})
    assert check_missing_evidence(data).status == "fail"


def test_evaluation_declarations_reject_invalid_status_and_missing_task_oracle():
    with pytest.raises(ValueError):
        EvaluationResult("looks_fine")
    with pytest.raises(ValueError, match="task correctness"):
        TaskCase("only_efficiency", "task", (EvaluationSpec("fast", "latency", "strategy",
            lambda data: EvaluationResult("pass")),))
    with pytest.raises(ValueError):
        TaskExecution("answer", metrics={"cost_usd": float("nan")})


def test_cli_custom_strategy_factory_is_injected_without_builtin_runners(monkeypatch, tmp_path):
    from research_agent import cli
    import sys
    calls = []
    case = simple_case(lambda data: EvaluationResult("pass"))
    monkeypatch.setattr(cli, "_evaluation_cases", lambda: [RegisteredCase("custom", case)])
    def factory(*, model):
        calls.append(model)
        return StrategySpec("custom_loop", lambda *a: TaskExecution("done"), metadata={"model": model})
    monkeypatch.setitem(sys.modules, "sample_eval_extension", SimpleNamespace(create=factory))
    result = CliRunner().invoke(cli.main, ["evaluation", "run", "--strategy-factory", "sample_eval_extension:create",
        "--model", "fixture-model", "--output", str(tmp_path / "custom")])
    assert result.exit_code == 0, result.output
    assert calls == ["fixture-model"]
    assert set(json.loads((tmp_path / "custom.json").read_text(encoding="utf-8"))["summary"]) == {"custom_loop"}


def test_partial_model_usage_remains_unknown_instead_of_underreporting():
    from research_agent.kernel import FunctionCallingKernel
    from research_agent.evaluation_runtime import runtime_strategy
    class Partial(ScriptedAdapter):
        def select(self, *args, **kwargs):
            result = super().select(*args, **kwargs)
            if self.count == 1:
                result.pop("usage")
            return result
    strategy = runtime_strategy(FunctionCallingKernel(), lambda: SimpleNamespace(model="scripted"), adapter_factory=Partial)
    report = run_benchmark([RegisteredCase("filesystem", filesystem_cases()[0])], [strategy])
    assert report["summary"]["function_calling"]["pass"] == 1
    assert report["runs"][0]["metrics"]["prompt_tokens"] is None
    assert report["runs"][0]["metrics"]["model_calls"] == 4
