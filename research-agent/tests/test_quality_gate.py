# tests/test_quality_gate.py — internal quality gate: parsing, evaluation, report.
import json
import os

from research_agent.quality import collect
from research_agent.quality.gate import DEFAULT_THRESHOLDS, evaluate
from research_agent.quality.report import (
    get_latest_report, load_history, render_html, write_report,
)

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="3" failures="1" errors="0" skipped="1">
    <testcase classname="tests.test_network_tools" name="test_validate_ssrf" time="0.1"/>
    <testcase classname="tests.test_network_tools" name="test_validate_block_private" time="0.1">
      <failure message="boom">traceback</failure>
    </testcase>
    <testcase classname="tests.test_memory" name="test_write" time="0.1">
      <skipped message="no vector"/>
    </testcase>
  </testsuite>
</testsuites>
"""

COVERAGE = {
    "meta": {"version": "7.0"},
    "files": {
        "src/research_agent/tools/builtin/network.py": {
            "summary": {"percent_covered": 99.0}},
        "src/research_agent/guardrail.py": {"summary": {"percent_covered": 85.0}},
        "src/research_agent/tools/__init__.py": {"summary": {"percent_covered": 72.5}},
    },
    "totals": {"percent_covered": 88.0},
}


def _write(tmp_path, name, text):
    p = os.path.join(str(tmp_path), name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return p


def test_parse_junit_counts(tmp_path):
    p = _write(tmp_path, "junit.xml", JUNIT)
    m = collect.parse_junit(p)
    assert m["total"] == 3
    assert m["passed"] == 1 and m["failed"] == 1 and m["skipped"] == 1
    assert m["by_file"]["tests/test_network_tools.py"]["failed"] == 1
    assert m["by_file"]["tests/test_memory.py"]["skipped"] == 1


def test_parse_coverage(tmp_path):
    p = _write(tmp_path, "cov.json", json.dumps(COVERAGE))
    m = collect.parse_coverage(p)
    assert m["total_percent"] == 88.0
    assert m["files"]["src/research_agent/guardrail.py"] == 85.0


def _metrics(passed=300, failed=0, skipped=2, coverage_files=None):
    tests = [{"file": "tests/test_network_tools.py",
              "name": f"test_validate_ssrf_{i}", "status": "passed"} for i in range(8)]
    return {
        "returncode": 0 if failed == 0 else 1,
        "tests": {"total": passed + failed + skipped, "passed": passed,
                  "failed": failed, "skipped": skipped, "tests": tests, "by_file": {}},
        "coverage": {"files": coverage_files if coverage_files is not None else {
            "src/research_agent/tools/builtin/network.py": 99.0,
            "src/research_agent/guardrail.py": 85.0,
            "src/research_agent/tools/__init__.py": 72.5,
        }, "total_percent": 88.0},
    }


def test_evaluate_passes_when_all_gates_met():
    rep = evaluate(_metrics())
    assert rep["overall"] == "pass"
    assert rep["summary"]["passed"] == rep["summary"]["total"]
    assert rep["coverage_total"] == 88.0


def test_evaluate_fails_on_failed_tests():
    rep = evaluate(_metrics(failed=1))
    assert rep["overall"] == "fail"
    failed = [c for c in rep["checks"] if c["id"] == "tests_failed"][0]
    assert failed["passed"] is False


def test_evaluate_fails_on_low_coverage():
    rep = evaluate(_metrics(coverage_files={
        "src/research_agent/tools/builtin/network.py": 40.0,
        "src/research_agent/guardrail.py": 85.0,
        "src/research_agent/tools/__init__.py": 72.5,
    }))
    assert rep["overall"] == "fail"
    cov = [c for c in rep["checks"] if c["id"].endswith("network.py")][0]
    assert cov["passed"] is False and cov["value"] == 40.0


def test_evaluate_security_requires_full_pass():
    m = _metrics()
    m["tests"]["tests"][0]["status"] = "failed"  # break 1 of 8 security tests
    rep = evaluate(m)
    sec = [c for c in rep["checks"] if c["id"] == "security"][0]
    assert sec["passed"] is False
    assert rep["overall"] == "fail"


def test_coverage_absent_is_skipped_not_failed():
    m = _metrics()
    m["coverage"] = None
    rep = evaluate(m)
    cov_checks = [c for c in rep["checks"] if c["category"] == "coverage"]
    assert cov_checks and all(c["passed"] for c in cov_checks)


def test_report_is_json_serializable():
    rep = evaluate(_metrics())
    assert json.loads(json.dumps(rep, ensure_ascii=False))["overall"] == "pass"


def test_render_html_contains_key_sections():
    rep = evaluate(_metrics())
    doc = render_html(rep, [rep])
    assert "PaperPilot 质量看板" in doc
    assert "PASS" in doc
    assert "关键模块覆盖率" in doc
    assert "趋势" in doc
    assert "test_validate_ssrf_0" not in doc  # per-test names are not dumped


def test_render_html_includes_runtime_section():
    rep = evaluate(_metrics())
    rep["runtime"] = {"overall": 88.0, "grade": "B", "sessions": 2, "tool_calls": 5,
                      "tool_errors": 1, "faults": 1, "completed": 2,
                      "dimensions": {"tool_health": 80.0, "completion": 100.0},
                      "issues": {"工具连续失败": 1}}
    doc = render_html(rep, [rep])
    assert "运行时框架审计" in doc
    assert "工具健康" in doc and "运行时框架分" in doc


def test_render_html_includes_telemetry_section():
    rep = evaluate(_metrics())
    rep["telemetry"] = {"runs": 3, "tokens": 9000, "cost_usd": 0.012,
                        "wall_ms": 45000, "completed": 3,
                        "avg": {"tokens": 3000, "cost_usd": 0.004, "wall_ms": 15000},
                        "p95": {"tokens": 5000, "cost_usd": 0.008, "wall_ms": 30000}}
    doc = render_html(rep, [rep])
    assert "运行时评测（成本/时延）" in doc
    assert "累计费用" in doc


def test_write_report_and_history(temp_data_dir):
    rep = evaluate(_metrics())
    paths = write_report(rep)
    assert os.path.exists(paths["json_path"])
    assert os.path.exists(paths["html_path"])
    hist = load_history(limit=10)
    assert hist and hist[-1]["overall"] == "pass"
    latest = get_latest_report()
    assert latest is not None and latest["overall"] == "pass"


def test_default_thresholds_sane():
    assert DEFAULT_THRESHOLDS["tests_failed_max"] == 0
    assert DEFAULT_THRESHOLDS["security_pass_rate"] == 100.0
    assert "tools/builtin/network.py" in DEFAULT_THRESHOLDS["coverage_min"]
