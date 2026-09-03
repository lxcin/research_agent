# tests/test_diagnostics_phase_e.py — semantic self-eval, scan, report, feedback
import json
import os

from research_agent.llm import MockLLMProvider
from research_agent.models import AgentState
from research_agent import validate as val
from research_agent.diagnostics import scan as diag_scan
from research_agent.diagnostics import report as diag_report
from research_agent.diagnostics import feedback as diag_feedback
from research_agent.diagnostics.recorder import EventRecorder
from research_agent.memory import get_manager, MemoryScope, MemoryKind


# ── E.1 rules ───────────────────────────────────────────────────────────────

def test_rule_check_detects_platitude():
    state = AgentState(user_input="写个综述", final_response="我可以帮你完成综述！")
    issues = val.rule_check_semantic(state)
    assert any(i["type"] == "platitude" for i in issues)


def test_rule_check_clean_response():
    state = AgentState(user_input="写个综述", final_response="已找到5篇相关论文，核心结论是...")
    assert val.rule_check_semantic(state) == []


def test_rule_check_ungrounded_citation():
    msgs = [{"role": "tool", "content": json.dumps({"paper_id": "paper_aaa111"})}]
    state = AgentState(user_input="x", final_response="见 paper:paper_bbb222 的结论")
    issues = val.rule_check_semantic(state, msgs)
    assert any(i["type"] == "ungrounded_citation" for i in issues)


def test_rule_check_grounded_citation_ok():
    msgs = [{"role": "tool", "content": json.dumps({"paper_id": "paper_aaa111"})}]
    state = AgentState(user_input="x", final_response="见 paper:paper_aaa111 的结论")
    assert val.rule_check_semantic(state, msgs) == []


# ── E.1 LLM check ───────────────────────────────────────────────────────────

def test_llm_check_detects_contradiction():
    llm = MockLLMProvider(['[{"issue": "contradiction", "detail": "工具失败却说成功"}]'])
    state = AgentState(user_input="跑测试", final_response="测试全部通过！")
    issues = val.llm_check_semantic(llm, state, tool_summary="pytest: 2 failed")
    assert any(i["type"] == "contradiction" for i in issues)


def test_llm_check_filters_invalid_issue():
    llm = MockLLMProvider(['[{"issue": "not_a_valid_one"}, {"issue": "placeholder"}]'])
    state = AgentState(user_input="x", final_response="y")
    issues = val.llm_check_semantic(llm, state)
    types = [i["type"] for i in issues]
    assert types == ["placeholder"]


def test_llm_check_garbage():
    llm = MockLLMProvider(["not json"])
    state = AgentState(user_input="x", final_response="y")
    assert val.llm_check_semantic(llm, state) == []


# ── E.2 scan ────────────────────────────────────────────────────────────────

def _seed_logs(data_dir, faults_by_run):
    import os as _os
    logs = _os.path.join(str(data_dir), "logs")
    _os.makedirs(logs, exist_ok=True)
    for i, faults in enumerate(faults_by_run):
        rec = EventRecorder(trace_id=f"run{i}", data_dir=data_dir)
        rec.record("start", {"input": "hello"})
        rec.record("reply", {"text": "完成了"})
        for f in faults:
            rec.record("fault", f)
    return logs


def test_scan_aggregates(temp_data_dir, monkeypatch):
    _seed_logs(temp_data_dir, [
        [{"kind": "empty_streak", "tool": "retrieve"}],
        [{"kind": "empty_streak", "tool": "retrieve"},
         {"kind": "tool_loop", "tool": "retrieve"}],
        [],
    ])
    result = diag_scan.scan(data_dir=temp_data_dir, limit=10)
    assert result["totals"]["sessions"] == 3
    assert result["totals"]["fault_kinds"].get("empty_streak") == 2
    assert result["totals"]["fault_kinds"].get("tool_loop") == 1


def test_scan_semantic_pass_with_llm(temp_data_dir):
    rec = EventRecorder(trace_id="s1", data_dir=temp_data_dir)
    rec.record("start", {"input": "帮我总结这篇文章"})
    for t in ["总结是", "该文", "主要讲"]:
        rec.record("reply", {"text": t})
    llm = MockLLMProvider(['[{"issue": "placeholder", "detail": "没有实质内容"}]'])
    result = diag_scan.scan(data_dir=temp_data_dir, limit=10, llm=llm)
    assert result["totals"]["semantic_issues"].get("placeholder", 0) >= 1


# ── E.3 report ──────────────────────────────────────────────────────────────

def test_report_write_and_render(temp_data_dir):
    rec = EventRecorder(trace_id="r1", data_dir=temp_data_dir)
    rec.record("fault", {"kind": "error_streak", "tool": "shell_exec"})
    scan_result = diag_scan.scan(data_dir=temp_data_dir, limit=10)
    out = diag_report.write_report(scan_result, data_dir=temp_data_dir)
    assert os.path.isfile(out["md_path"])
    assert os.path.isfile(out["json_path"])
    assert "工具连续失败" in out["markdown"] or "error_streak" in out["markdown"]
    assert diag_report.latest_report(data_dir=temp_data_dir) == out["md_path"]


# ── E.5 feedback to Tier B memory ───────────────────────────────────────────

def test_ingest_fault_lessons_writes_dead_end(temp_data_dir):
    mgr = get_manager()
    n = diag_feedback.ingest_fault_lessons(
        {"error_streak": 3, "tool_loop": 4, "empty_streak": 1},
        mgr=mgr, min_count=2)
    assert n >= 2
    units = mgr.list_units(scope=MemoryScope.USER, kind=MemoryKind.DEAD_END)
    assert any("error_streak" in u.source.get("fault", "") for u in units)
    assert all("empty_streak" not in (u.source.get("fault", "")) for u in units)


def test_ingest_fault_lessons_dedup(temp_data_dir):
    mgr = get_manager()
    diag_feedback.ingest_fault_lessons({"no_response": 5}, mgr=mgr, min_count=2)
    first = mgr.count()
    diag_feedback.ingest_fault_lessons({"no_response": 5}, mgr=mgr, min_count=2)
    assert mgr.count() == first


# ── E.4 CLI diagnose command smoke ──────────────────────────────────────────

def test_cli_diagnose_smoke(temp_data_dir, monkeypatch):
    from click.testing import CliRunner
    from research_agent import cli as cli_mod
    _seed_logs(temp_data_dir, [[{"kind": "search_exhausted", "tool": "search_papers"}]])
    monkeypatch.setattr("research_agent.config.get_data_dir", lambda: temp_data_dir)
    runner = CliRunner()
    result = runner.invoke(cli_mod.diagnose, ["--limit", "10"])
    assert result.exit_code == 0
    assert "search_exhausted" in result.output or "搜索" in result.output

