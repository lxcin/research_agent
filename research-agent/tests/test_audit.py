# tests/test_audit.py — full-chain framework audit: trace, review, score, report.
import json
import os

from research_agent.diagnostics import audit as audit_mod


def ev(et, data):
    return {"trace": "t1", "workspace": "/ws", "chat": "c1", "event": et, "data": data}


def _clean_session():
    return [
        ev("start", {"input": "do x"}),
        ev("tool_start", {"id": "1", "name": "file_read", "input": "{'path': 'a'}"}),
        ev("tool_end", {"id": "1", "name": "file_read", "status": "success"}),
        ev("tool_start", {"id": "2", "name": "file_write", "input": "{'path': 'b'}"}),
        ev("tool_end", {"id": "2", "name": "file_write", "status": "success"}),
        ev("reply", {"text": "完成"}),
        ev("end", {"final_response": "完成"}),
    ]


def _messy_session():
    return [
        ev("start", {"input": "do y"}),
        ev("tool_start", {"id": "1", "name": "shell_exec", "input": "{}"}),
        ev("tool_end", {"id": "1", "name": "shell_exec", "status": "error"}),
        ev("tool_start", {"id": "2", "name": "shell_exec", "input": "{}"}),
        ev("tool_end", {"id": "2", "name": "shell_exec", "status": "error"}),
        ev("tool_start", {"id": "3", "name": "shell_exec", "input": "{}"}),
        ev("tool_end", {"id": "3", "name": "shell_exec", "status": "error"}),
        ev("fault", {"kind": "tool_loop", "tool": "shell_exec", "repeats": 4}),
        ev("end", {"final_response": ""}),
    ]


def test_build_chain_counts():
    ch = audit_mod.build_chain(_clean_session())
    assert ch["tool_calls"] == 2 and ch["tool_errors"] == 0
    assert ch["has_reply"] is True and ch["fault_count"] == 0
    assert ch["steps"] == ["file_read", "file_write"]


def test_build_chain_flags_errors_and_redundancy():
    ch = audit_mod.build_chain(_messy_session())
    assert ch["tool_errors"] == 3
    assert ch["redundant"] == 2  # three identical consecutive calls
    assert ch["fault_kinds"].get("tool_loop") == 1
    assert ch["has_reply"] is False


def test_score_clean_is_high():
    ch = audit_mod.build_chain(_clean_session())
    sc = audit_mod.score_chain(ch)
    assert sc["overall"] == 100.0 and sc["grade"] == "A"


def test_score_messy_is_low_with_issues():
    ch = audit_mod.build_chain(_messy_session())
    sc = audit_mod.score_chain(ch)
    assert sc["overall"] < 60 and sc["grade"] == "D"
    issues = audit_mod.review_chain(ch)
    assert any("重复" in i or "工具" in i for i in issues)
    assert any("最终回复" in i for i in issues)


def test_audit_aggregates_logs(tmp_path):
    logs = os.path.join(str(tmp_path), "logs")
    os.makedirs(logs)
    for name, sess in (("s1.jsonl", _clean_session()), ("s2.jsonl", _messy_session())):
        with open(os.path.join(logs, name), "w", encoding="utf-8") as fh:
            for e in sess:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")

    result = audit_mod.audit(limit=10, data_dir=str(tmp_path))
    assert result["totals"]["sessions"] == 2
    assert result["totals"]["tool_calls"] == 5
    assert result["totals"]["completed"] == 1
    assert 0 < result["totals"]["overall"] < 100
    assert result["issues"]

    md = audit_mod.render_markdown(result)
    assert "全链路审查评分报告" in md and "维度评分" in md and "链:" in md


def test_build_chain_counts_shell_failure_as_error():
    events = [ev("start", {}),
              ev("tool_start", {"id": "1", "name": "shell_exec", "input": "bad"}),
              ev("tool_end", {"id": "1", "name": "shell_exec", "status": "success",
                              "output": {"success": False, "returncode": 1}}),
              ev("reply", {"text": "done"})]
    ch = audit_mod.build_chain(events)
    assert ch["tool_errors"] == 1
    assert ch["tool_calls"] == 1


def test_audit_economy_from_telemetry(temp_data_dir):
    import os as _os
    from research_agent import telemetry
    logs = _os.path.join(str(temp_data_dir), "logs")
    _os.makedirs(logs, exist_ok=True)
    with open(_os.path.join(logs, "s1.jsonl"), "w", encoding="utf-8") as fh:
        for e in _clean_session():
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    telemetry.begin_run(trace="t1")
    telemetry.note_llm_call("deepseek-chat", {"prompt_tokens": 100, "completion_tokens": 100},
                            5.0, "tool_select")
    telemetry.end_run("ok")

    result = audit_mod.audit(limit=10)
    assert result["economy"]["score"] is not None
    assert result["totals"]["economy"] is not None
    md = audit_mod.render_markdown(result)
    assert "经济性" in md


def test_audit_empty_and_report(tmp_path):
    result = audit_mod.audit(limit=10, data_dir=str(tmp_path))
    assert result["totals"]["sessions"] == 0 and result["totals"]["overall"] == 0.0
    out = audit_mod.write_report(result, out_dir=str(tmp_path))
    assert os.path.isfile(out["md_path"]) and os.path.isfile(out["json_path"])
    assert "框架总分" in out["markdown"]
