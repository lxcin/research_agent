# tests/test_telemetry.py — runtime evaluation telemetry (plugin + metered runtime).
import os
import tempfile
import types

from research_agent import telemetry


class _Usage:
    def __init__(self, pt, ct):
        self.prompt_tokens = pt
        self.completion_tokens = ct


def test_estimate_cost_known_and_unknown():
    cost, known = telemetry.estimate_cost("deepseek-chat", 1_000_000, 0)
    assert known and cost == 0.27
    cost2, known2 = telemetry.estimate_cost("openai/some-unknown-model", 100, 100)
    assert not known2 and cost2 == 0.0


def test_collect_run_tokens_cost_path(temp_data_dir):
    telemetry.begin_run(trace="t1", workspace="/ws", chat="c1")
    telemetry.observe("tool_start", {"id": "1", "name": "web_search"})
    telemetry.observe("tool_end", {"id": "1", "name": "web_search", "status": "success"})
    telemetry.observe("fault", {"kind": "tool_loop"})
    telemetry.note_llm_call("deepseek-chat", _Usage(1000, 500), 120.0, "tool_select")
    telemetry.observe("llm_usage", {"model": "deepseek-chat", "prompt_tokens": 200,
                                    "completion_tokens": 800, "latency_ms": 300.0,
                                    "purpose": "answer"})
    rec = telemetry.end_run("done")

    assert rec["tokens"] == {"prompt": 1200, "completion": 1300, "total": 2500}
    assert rec["rounds"] == 1
    assert rec["path"] == ["web_search"]
    assert rec["faults"] == {"tool_loop": 1}
    assert rec["completed"] is True
    assert rec["cost_usd"] > 0
    assert rec["phases_ms"]["execution"] >= 0


def test_cache_hit_tokens_recorded(temp_data_dir):
    telemetry.begin_run(trace="ch1")
    telemetry.note_llm_call("deepseek-chat", {
        "prompt_tokens": 1000, "completion_tokens": 10,
        "prompt_tokens_details": {"cached_tokens": 800}}, 5.0, "tool_select")
    rec = telemetry.end_run("ok")
    assert rec["cached_tokens"] == 800
    assert rec["cache_hit_rate"] == 0.8
    rep = telemetry.report()
    assert rep["totals"]["cached_tokens"] == 800
    assert rep["totals"]["cache_hit_rate"] == 0.8
    assert "缓存命中" in telemetry.render_markdown(rep)


def test_estimate_cost_applies_cache_discount():
    cost, known = telemetry.estimate_cost("deepseek-chat", 1_000_000, 0,
                                          cached_tokens=800_000)
    assert known
    # 200k miss*0.27 + 800k cache*0.07 = 0.054 + 0.056 = 0.11
    assert cost == 0.11


def test_llm_call_bills_cache_cheaper(temp_data_dir):
    telemetry.begin_run(trace="cc")
    telemetry.note_llm_call("deepseek-chat",
                            {"prompt_tokens": 1000, "completion_tokens": 0,
                             "cached_tokens": 800}, 1.0, "tool_select")
    rec = telemetry.end_run("ok")
    assert rec["cost_usd"] == round((200 * 0.27 + 800 * 0.07) / 1_000_000, 6)


def test_usage_dict_preserves_cache_fields():
    from research_agent.agent import _usage_dict

    class _Details:
        cached_tokens = 1152

    class _U:
        prompt_tokens = 1331
        completion_tokens = 1
        prompt_cache_hit_tokens = 1152
        prompt_tokens_details = _Details()

    d = _usage_dict(_U())
    assert d["prompt_tokens"] == 1331 and d["completion_tokens"] == 1
    assert d["prompt_cache_hit_tokens"] == 1152
    assert d["prompt_tokens_details"]["cached_tokens"] == 1152
    # and telemetry now sees it
    telemetry.begin_run(trace="ud")
    telemetry.note_llm_call("deepseek-chat", d, 1.0, "tool_select")
    rec = telemetry.end_run("ok")
    assert rec["cached_tokens"] == 1152 and rec["cache_hit_rate"] == round(1152 / 1331, 4)


def test_run_records_context_growth_metrics(temp_data_dir):
    telemetry.begin_run(trace="g1")
    telemetry.note_llm_call("deepseek-chat",
                            {"prompt_tokens": 100, "completion_tokens": 10}, 1.0, "tool_select")
    telemetry.note_llm_call("deepseek-chat",
                            {"prompt_tokens": 500, "completion_tokens": 10}, 1.0, "tool_select")
    rec = telemetry.end_run("ok")
    assert rec["rounds"] == 2
    assert rec["max_prompt_tokens"] == 500
    assert rec["tokens_per_round"] == round((600 + 20) / 2, 1)


def test_cache_unreported_is_none(temp_data_dir):
    telemetry.begin_run(trace="n1")
    telemetry.note_llm_call("deepseek-chat",
                            {"prompt_tokens": 500, "completion_tokens": 5}, 1.0, "tool_select")
    rec = telemetry.end_run("ok")
    assert rec["cache_hit_rate"] is None
    assert telemetry.report()["totals"]["cache_hit_rate"] is None
    assert "n/a" in telemetry.render_markdown(telemetry.report())


def test_tool_success_by_result_payload(temp_data_dir):
    telemetry.begin_run(trace="t9")
    telemetry.observe("tool_start", {"id": "1", "name": "shell_exec"})
    telemetry.observe("tool_end", {"id": "1", "name": "shell_exec", "status": "success",
                                   "output": {"success": False, "returncode": 1}})
    telemetry.observe("tool_start", {"id": "2", "name": "file_write"})
    telemetry.observe("tool_end", {"id": "2", "name": "file_write", "status": "success",
                                   "output": {"path": "a", "size": 1}})
    rec = telemetry.end_run("ok")
    assert rec["tools_total"] == 2 and rec["tools_ok"] == 1


def test_post_run_llm_attributed(temp_data_dir):
    telemetry.begin_run(trace="p9")
    telemetry.note_llm_call("deepseek-chat",
                            {"prompt_tokens": 100, "completion_tokens": 10}, 1.0, "tool_select")
    telemetry.end_run("ok")
    # a post-run call (memory distillation) after the metered run ended
    telemetry.note_llm_call("deepseek-chat",
                            {"prompt_tokens": 200, "completion_tokens": 20}, 1.0, "extract")
    posts = telemetry.load_posts()
    assert posts and posts[-1]["purpose"] == "extract"
    rep = telemetry.report()
    assert rep["post"]["calls"] == 1
    assert rep["totals"]["tokens"] == 110 + 220
    assert "回合后" in telemetry.render_markdown(rep)


def test_runs_persisted_and_report(temp_data_dir):
    telemetry.begin_run(trace="t2")
    telemetry.note_llm_call("deepseek-chat", _Usage(100, 100), 10.0, "tool_select")
    telemetry.end_run("ok")
    assert os.path.isfile(telemetry.runs_path())

    rep = telemetry.report()
    assert rep["runs"] == 1 and rep["totals"]["tokens"] == 200
    md = telemetry.render_markdown(rep)
    assert "运行时评测报告" in md and "费用归因" in md
    out = telemetry.write_report(rep)
    assert os.path.isfile(out["md_path"]) and os.path.isfile(out["json_path"])
    assert telemetry.query_run("t2") is not None
    assert telemetry.query_run("nope") is None


def test_metered_runtime_observes_without_touching_loop(temp_data_dir):
    # a fake base runtime that uses only the public ctx contract
    def fake_base_run(ctx):
        ctx.call_llm_with_tools(ctx.llm, [], [], "auto")
        ctx.emit("tool_start", {"id": "x", "name": "file_read"})
        ctx.emit("tool_end", {"id": "x", "name": "file_read", "status": "success"})
        ctx.state.final_response = "answer"
        return ctx.state

    base = types.SimpleNamespace(run=fake_base_run)

    def call_llm(llm, messages, tools, tool_choice="auto"):
        return {"content": "", "model": "deepseek-chat",
                "usage": {"prompt_tokens": 300, "completion_tokens": 100}}

    ctx = types.SimpleNamespace(
        llm=None, call_llm_with_tools=call_llm,
        emit=lambda et, d: None, workspace_dir="/ws", chat_id="c9",
        state=types.SimpleNamespace(final_response=""))

    telemetry.MeteredRuntime(base).run(ctx)
    rec = telemetry.query_run("c9")
    assert rec is not None
    assert rec["tokens"]["total"] == 400
    assert rec["path"] == ["file_read"]
    assert rec["completed"] is True   # finalize reads ctx.state.final_response


def test_price_snapshot_recorded(temp_data_dir):
    telemetry.begin_run(trace="p1")
    telemetry.note_llm_call("deepseek-chat", _Usage(1, 1), 1.0, "tool_select")
    rec = telemetry.end_run("ok")
    assert rec["prices_version"]
    import os as _os
    snap = _os.path.join(str(telemetry.get_data_dir()), "usage", "prices.json")
    assert _os.path.isfile(snap)
    assert "prices_per_1m_usd" in open(snap, encoding="utf-8").read()


def test_memory_distill_switch(monkeypatch, temp_data_dir):
    from research_agent import agent as ag
    from research_agent.models import AgentState
    import research_agent.config as cfg
    called = []
    monkeypatch.setattr(ag, "_turns_token_count", lambda *a, **k: 0)
    # distill off → no pipeline submit
    monkeypatch.setattr(cfg, "get_memory_config",
                        lambda: {"enabled": True, "distill": False})
    import research_agent.memory.pipeline as pl
    monkeypatch.setattr(pl, "submit", lambda *a, **k: called.append(1))
    st = AgentState(user_input="x")
    ag._maybe_distill(st, "/ws", "c", None)
    assert called == []
    # distill on → submit reached (source empty → still no submit; just assert no crash)
    monkeypatch.setattr(cfg, "get_memory_config",
                        lambda: {"enabled": True, "distill": True})
    ag._maybe_distill(st, "", "", None)   # empty ws/chat → early return
    assert called == []


def test_telemetry_plugin_registered_and_handlers(temp_data_dir):
    from research_agent.tools import get_registry
    from research_agent.tools.builtin import register_builtins
    from research_agent.tools.builtin.telemetry import _handle_usage_report, _handle_usage_query
    register_builtins()
    reg = get_registry()
    assert "telemetry" in reg.plugins
    assert "usage_report" in reg and "usage_query" in reg
    assert reg.tools["usage_report"].side_effect is False

    telemetry.begin_run(trace="h1")
    telemetry.note_llm_call("deepseek-chat", _Usage(10, 10), 5.0, "tool_select")
    telemetry.end_run("ok")
    assert _handle_usage_report({}, None, None, lambda *a: None).success
    q = _handle_usage_query({"trace_id": "h1"}, None, None, lambda *a: None)
    assert q.success and q.data["trace"] == "h1"
    assert not _handle_usage_query({}, None, None, lambda *a: None).success
