# tests/test_diagnostics.py — Phase D: recorder / monitor / summary
import os

from research_agent.diagnostics.recorder import EventRecorder, _clip
from research_agent.diagnostics.monitor import RunMonitor, _fault_text
from research_agent.diagnostics import summary as diag_summary


# ── D.1 recorder ────────────────────────────────────────────────────────────

def test_recorder_writes_jsonl(temp_data_dir):
    rec = EventRecorder(trace_id="trace-abc", workspace_dir="/ws", chat_id="c1")
    rec.record("tool_start", {"name": "retrieve", "input": {"query": "x"}})
    rec.record("thinking", {"text": "hello"})
    assert rec.path and os.path.isfile(rec.path)
    events = diag_summary.load_log(rec.path)
    assert len(events) == 2
    assert events[0]["event"] == "tool_start"
    assert events[0]["trace"] == "trace-abc"
    assert events[0]["workspace"] == "/ws"


def test_recorder_truncates_large_fields(temp_data_dir):
    rec = EventRecorder(trace_id="t1")
    big = "x" * 5000
    rec.record("tool_end", {"output": {"stdout": big, "list": list(range(1000))}})
    events = diag_summary.load_log(rec.path)
    assert len(events[0]["data"]["output"]["stdout"]) <= 1000
    assert len(events[0]["data"]["output"]["list"]) <= 100


def test_recorder_wrap_forwards_and_records(temp_data_dir):
    seen = []
    rec = EventRecorder(trace_id="t2")
    emit = rec.wrap(lambda et, d: seen.append((et, d)))
    emit("reply", {"text": "hi"})
    assert seen == [("reply", {"text": "hi"})]
    assert len(diag_summary.load_log(rec.path)) == 1


def test_recorder_disabled(temp_data_dir):
    rec = EventRecorder(trace_id="t3", enabled=False)
    rec.record("thinking", {"text": "x"})
    assert rec.path is None


# ── D.3 monitor ─────────────────────────────────────────────────────────────

def test_monitor_empty_streak():
    m = RunMonitor(empty_streak_limit=3)
    m.observe("tool", {"tool": "retrieve", "status": "local_empty"})
    m.observe("tool", {"tool": "retrieve", "status": "local_empty"})
    assert m.faults == []
    m.observe("tool", {"tool": "retrieve", "status": "local_empty"})
    assert any(f["kind"] == "empty_streak" for f in m.faults)


def test_monitor_tool_loop_same_params():
    m = RunMonitor(tool_loop_limit=4)
    params = {"query": "same query"}
    for _ in range(4):
        m.observe("tool_start", {"name": "retrieve", "input": params})
    assert any(f["kind"] == "tool_loop" for f in m.faults)


def test_monitor_tool_loop_different_params_no_fault():
    m = RunMonitor(tool_loop_limit=4)
    for i in range(4):
        m.observe("tool_start", {"name": "retrieve", "input": {"query": f"q{i}"}})
    assert all(f["kind"] != "tool_loop" for f in m.faults)


def test_monitor_error_streak():
    m = RunMonitor(error_streak_limit=3)
    for _ in range(3):
        m.observe("tool_end", {"name": "shell_exec", "status": "error"})
    assert any(f["kind"] == "error_streak" for f in m.faults)


def test_monitor_search_exhausted_via_thinking():
    m = RunMonitor()
    m.observe("thinking", {"text": "检索次数已达上限(10)，选最好的论文..."})
    assert any(f["kind"] == "search_exhausted" for f in m.faults)


def test_monitor_llm_unstable_via_thinking():
    m = RunMonitor()
    m.observe("thinking", {"text": "模型调用失败 (尝试 2/5): timeout"})
    assert any(f["kind"] == "llm_unstable" for f in m.faults)


def test_monitor_no_response():
    m = RunMonitor()
    faults = m.finalize(final_response="   ")
    assert any(f["kind"] == "no_response" for f in faults)


def test_monitor_finalize_with_response_no_fault():
    m = RunMonitor()
    faults = m.finalize(final_response="完成了")
    assert all(f["kind"] != "no_response" for f in faults)


def test_fault_text_readable():
    assert "检索返回空" in _fault_text({"kind": "empty_streak", "streak": 4, "tool": "retrieve"})
    assert _fault_text({"kind": "tool_loop", "tool": "x"}) != ""


# ── D.2 summary ─────────────────────────────────────────────────────────────

def test_summary_aggregates_events(temp_data_dir):
    rec = EventRecorder(trace_id="run1", workspace_dir="/w", chat_id="chat1")
    rec.record("tool_start", {"name": "retrieve"})
    rec.record("tool_end", {"name": "retrieve", "status": "success"})
    rec.record("tool_start", {"name": "shell_exec"})
    rec.record("tool_end", {"name": "shell_exec", "status": "error"})
    rec.record("fault", {"kind": "error_streak", "tool": "shell_exec"})
    s = diag_summary.summarize_file(rec.path)
    assert s["event_count"] == 5
    assert s["tool_attempts"] == 2
    assert s["tool_errors"] == 1
    assert s["success_rate"] == 0.5
    assert s["fault_kinds"].get("error_streak") == 1
    assert s["trace_id"] == "run1"


def test_list_logs_order(temp_data_dir):
    p1 = EventRecorder(trace_id="first").path
    p2 = EventRecorder(trace_id="second").path
    # file created on first record()
    EventRecorder(trace_id="first").record("start", {})
    EventRecorder(trace_id="second").record("start", {})
    assert os.path.isfile(p1) and os.path.isfile(p2)
    logs_dir = os.path.dirname(p1)
    files = diag_summary.list_logs(logs_dir)
    assert len(files) == 2


def test_summarize_empty_file():
    assert diag_summary.summarize_file("C:/nope/none.jsonl")["event_count"] == 0
