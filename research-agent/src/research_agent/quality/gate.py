"""Internal quality gate — evaluate metrics against thresholds.

Thresholds are deliberately conservative so the gate is a *floor*, not a vanity
metric. The security gate counts the SSRF/URL-policy test subset in
tests/test_network_tools.py and requires a 100% pass rate.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone

DEFAULT_THRESHOLDS = {
    # correctness
    "tests_failed_max": 0,
    "tests_passed_min": 250,
    # per-module coverage floor (matched by path suffix)
    "coverage_min": {
        "tools/builtin/network.py": 90.0,
        "guardrail.py": 50.0,
        "tools/__init__.py": 60.0,
    },
    # security subset (test name contains one of these)
    "security_patterns": ("ssrf", "block", "validate", "scheme", "redirect",
                          "private", "loopback", "metadata", "mapped", "6to4", "nat64"),
    "security_min_tests": 8,
    "security_pass_rate": 100.0,
}


def _git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, errors="replace", timeout=5)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _check(cid, label, category, value, threshold, passed, unit="", detail=""):
    return {"id": cid, "label": label, "category": category, "value": value,
            "threshold": threshold, "passed": bool(passed), "unit": unit,
            "detail": detail}


def evaluate(metrics: dict, thresholds: dict | None = None) -> dict:
    """Turn raw metrics into a report with per-check pass/fail and overall status."""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    tests = metrics.get("tests") or {}
    cov = metrics.get("coverage") or None
    checks: list[dict] = []

    failed = int(tests.get("failed", 0))
    checks.append(_check(
        "tests_failed", "测试失败数", "correctness", failed, th["tests_failed_max"],
        failed <= th["tests_failed_max"], "个",
        "pytest 返回码 %s" % metrics.get("returncode")))

    passed = int(tests.get("passed", 0))
    checks.append(_check(
        "tests_passed", "测试通过数", "correctness", passed, th["tests_passed_min"],
        passed >= th["tests_passed_min"], "个"))

    cov_files = (cov or {}).get("files", {}) if cov else {}
    for suffix, minpct in th["coverage_min"].items():
        val = next((v for k, v in cov_files.items()
                    if k.replace("\\", "/").endswith(suffix)), None)
        if val is None:
            checks.append(_check(f"cov::{suffix}", f"覆盖率 {suffix}", "coverage",
                                 None, minpct, True, "%", "未采集（跳过）"))
        else:
            checks.append(_check(f"cov::{suffix}", f"覆盖率 {suffix}", "coverage",
                                 round(val, 1), minpct, val >= minpct, "%"))

    pats = th["security_patterns"]
    sec = [t for t in tests.get("tests", [])
           if t.get("file", "").endswith("test_network_tools.py")
           and any(p in t.get("name", "").lower() for p in pats)]
    sec_total = len(sec)
    sec_pass = sum(1 for t in sec if t["status"] == "passed")
    rate = (sec_pass / sec_total * 100.0) if sec_total else 0.0
    sec_ok = sec_total >= th["security_min_tests"] and rate >= th["security_pass_rate"]
    checks.append(_check("security", "安全用例通过率", "security", round(rate, 1),
                         th["security_pass_rate"], sec_ok, "%", f"{sec_pass}/{sec_total}"))

    all_pass = all(c["passed"] for c in checks)
    return {
        "overall": "pass" if all_pass else "fail",
        "checks": checks,
        "summary": {"passed": sum(1 for c in checks if c["passed"]), "total": len(checks)},
        "tests": {k: tests.get(k, 0) for k in ("total", "passed", "failed", "skipped")},
        "coverage_total": (cov or {}).get("total_percent") if cov else None,
        "coverage_files": cov_files,
        "thresholds": th,
        "returncode": metrics.get("returncode"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "commit": _git_commit(),
        "stdout_tail": (metrics.get("stdout_tail") or "")[:800],
    }


def _runtime_summary(rt: dict) -> dict:
    t = rt.get("totals", {}) or {}
    return {
        "overall": t.get("overall", 0.0),
        "grade": t.get("grade", "D"),
        "sessions": t.get("sessions", 0),
        "tool_calls": t.get("tool_calls", 0),
        "tool_errors": t.get("tool_errors", 0),
        "faults": t.get("faults", 0),
        "completed": t.get("completed", 0),
        "dimensions": t.get("dimensions", {}),
        "issues": rt.get("issues", {}),
    }


def run(workdir: str = ".", tests_path: str = "tests/", thresholds: dict | None = None,
        metrics: dict | None = None, include_runtime: bool = True) -> dict:
    """Collect metrics (unless provided) and evaluate the gate.

    Also attaches a runtime framework-audit summary (from data_dir/logs) so the
    dashboard shows build-time quality and runtime effectiveness side by side.
    """
    if metrics is None:
        from research_agent.quality import collect
        metrics = collect.run_pytest(workdir=workdir, tests_path=tests_path)
    report = evaluate(metrics, thresholds)
    if include_runtime:
        try:
            from research_agent import telemetry
            tel = telemetry.report(limit=50)      # read runs.jsonl once
        except Exception:
            tel = None
        try:
            from research_agent.diagnostics import audit as audit_mod
            report["runtime"] = _runtime_summary(
                audit_mod.audit(limit=20, telemetry_rep=tel))
        except Exception:
            report["runtime"] = None
        report["telemetry"] = (
            {"runs": tel["runs"], **tel["totals"], "avg": tel["avg"], "p95": tel["p95"]}
            if tel else None)
    return report


def _fmt_value(c: dict) -> str:
    if c["value"] is None:
        return "n/a"
    return f"{c['value']}{c.get('unit', '')}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="PaperPilot internal quality gate")
    p.add_argument("--workdir", default=".")
    p.add_argument("--tests", default="tests/")
    p.add_argument("--json", default="", help="write report JSON to this path")
    p.add_argument("--no-report", action="store_true",
                   help="do not write JSON/HTML artifacts to data_dir/quality")
    p.add_argument("--no-fail", action="store_true",
                   help="always exit 0 (report only)")
    a = p.parse_args(argv)

    report = run(a.workdir, a.tests)

    print(f"Quality gate: {report['overall'].upper()}  "
          f"({report['summary']['passed']}/{report['summary']['total']} checks)")
    for c in report["checks"]:
        mark = "PASS" if c["passed"] else "FAIL"
        th = "" if c["threshold"] is None else f" (>= {c['threshold']}{c.get('unit','')})"
        print(f"  [{mark}] {c['label']}: {_fmt_value(c)}{th} {c['detail']}")

    if a.json:
        import os
        os.makedirs(os.path.dirname(a.json) or ".", exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    if not a.no_report:
        from research_agent.quality.report import write_report
        paths = write_report(report)
        print(f"  report: {paths['html_path']}")

    if report["overall"] == "pass" or a.no_fail:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
