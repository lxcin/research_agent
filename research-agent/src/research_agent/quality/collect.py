"""Collect deterministic quality metrics for the internal quality gate.

Runs the test suite once with JUnit XML output (per-test pass/fail) plus an
optional coverage JSON, then parses both. No network and no API key are needed.

pytest-cov is optional: when it is not installed the coverage checks are reported
as "unavailable" (skipped) instead of failing the build.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


def parse_junit(xml_path: str) -> dict:
    """Parse a pytest JUnit XML file into counts + per-test/per-file detail."""
    root = ET.parse(xml_path).getroot()
    if root.tag == "testsuite":
        suites = [root]
    else:
        suites = root.findall("testsuite")

    tests: list[dict] = []
    for suite in suites:
        for tc in suite.findall("testcase"):
            classname = tc.get("classname", "") or ""
            file = classname.replace(".", "/") + ".py" if classname else ""
            if tc.find("failure") is not None or tc.find("error") is not None:
                status = "failed"
            elif tc.find("skipped") is not None:
                status = "skipped"
            else:
                status = "passed"
            tests.append({"file": file, "name": tc.get("name", ""), "status": status})

    by_file: dict[str, dict] = {}
    for t in tests:
        b = by_file.setdefault(t["file"], {"total": 0, "passed": 0, "failed": 0, "skipped": 0})
        b["total"] += 1
        b[t["status"]] += 1

    return {
        "total": len(tests),
        "passed": sum(1 for t in tests if t["status"] == "passed"),
        "failed": sum(1 for t in tests if t["status"] == "failed"),
        "skipped": sum(1 for t in tests if t["status"] == "skipped"),
        "tests": tests,
        "by_file": by_file,
    }


def parse_coverage(json_path: str) -> dict:
    """Parse a coverage.py JSON report into {file: percent} + overall total."""
    with open(json_path, encoding="utf-8") as fh:
        data = json.load(fh)
    files: dict[str, float] = {}
    for path, info in (data.get("files") or {}).items():
        summary = info.get("summary") or {}
        pct = summary.get("percent_covered", summary.get("percent_covered_display"))
        try:
            files[path.replace("\\", "/")] = float(pct)
        except (TypeError, ValueError):
            continue
    total = (data.get("totals") or {}).get("percent_covered")
    return {"files": files, "total_percent": float(total) if total is not None else None}


def _kill_tree(proc) -> None:
    """Best-effort kill of a process and its children (never raises)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


def run_pytest(workdir: str = ".", tests_path: str = "tests/", timeout: int = 600) -> dict:
    """Run pytest once with JUnit XML + coverage JSON and return parsed metrics."""
    tmp = tempfile.mkdtemp(prefix="pp-qgate-")
    junit = os.path.join(tmp, "junit.xml")
    cov = os.path.join(tmp, "coverage.json")

    env = dict(os.environ)
    env.setdefault("PYTHONPATH", "src")
    # Isolate the child pytest from the caller's data dir (the parent uses it for
    # the runtime audit; leaking it into the test run can crash native deps).
    env["RESEARCH_AGENT_DATA_DIR"] = os.path.join(tmp, "data")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    cmd = [sys.executable, "-m", "pytest", tests_path, "-q", "-p", "no:cacheprovider",
           f"--junitxml={junit}"]
    has_cov = False
    try:
        import pytest_cov  # noqa: F401
        has_cov = True
        cmd += ["--cov=research_agent", f"--cov-report=json:{cov}"]
    except Exception:
        pass

    # Redirect child stdout/stderr to FILES (not pipes): the test suite may spawn
    # long-lived grandchildren whose inherited pipe handle would otherwise keep
    # subprocess.run waiting forever (Windows pipe-inheritance deadlock).
    out_path = os.path.join(tmp, "pytest.out")
    err_path = os.path.join(tmp, "pytest.err")
    returncode, timed_out = 1, False
    with open(out_path, "w", encoding="utf-8", errors="replace") as out, \
            open(err_path, "w", encoding="utf-8", errors="replace") as err:
        proc = subprocess.Popen(cmd, cwd=workdir, env=env,
                                stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        try:
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            returncode, timed_out = 124, True

    def _read(path):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""

    stdout, stderr = _read(out_path), _read(err_path)
    if timed_out:
        stderr += f"\npytest timed out after {timeout}s"

    metrics = {
        "returncode": returncode,
        "stdout_tail": (stdout or "")[-2000:],
        "stderr_tail": (stderr or "")[-1000:],
        "tests": parse_junit(junit) if os.path.exists(junit) else {
            "total": 0, "passed": 0, "failed": 0, "skipped": 0, "tests": [], "by_file": {}},
        "coverage": parse_coverage(cov) if (has_cov and os.path.exists(cov)) else None,
    }
    return metrics
