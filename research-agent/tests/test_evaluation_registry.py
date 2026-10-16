from research_agent.evaluation import EvaluationSpec, EvaluationResult, EvaluationInput
from research_agent.tools import ToolRegistry
from research_agent.tools.schema import ToolPlugin, ToolSchema, ToolResult


def test_plugin_and_tool_evaluations_follow_lifecycle():
    registry = ToolRegistry()
    spec = EvaluationSpec("artifact", "output", "task", lambda data:
                          EvaluationResult("pass" if data.artifacts.get("correct") else "fail"))
    tool = ToolSchema("custom", "custom evaluator tool", {}, lambda *a: ToolResult.ok(),
                      plugin_id="example", evaluations=[spec])
    registry.install_plugin(ToolPlugin("example", tools=[tool], evaluations=[spec]), apply_enabled=False)
    results = registry.evaluate(artifacts={"correct": True})
    assert {r["owner"] for r in results if r["id"] == "artifact"} == {"example", "example/custom"}
    registry.disable_plugin("example")
    assert not any(r["id"] == "artifact" for r in registry.evaluate())
    registry.enable_plugin("example")
    assert sum(r["id"] == "artifact" for r in registry.evaluate()) == 2


def test_harness_does_not_invent_success_without_evidence():
    registry = ToolRegistry()
    assert all(r["status"] == "skip" for r in registry.evaluate())
    results = registry.evaluate(artifacts={"resource_scopes": [["acquire", "drain", "cleanup"]]})
    assert next(r for r in results if r["id"] == "drain")["status"] == "fail"
    results = registry.evaluate(artifacts={"resource_scopes": [["drain", "acquire"]]})
    assert next(r for r in results if r["id"] == "admission")["status"] == "fail"
