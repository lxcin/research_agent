# tests/test_context_trim.py — mid-turn context trim + run budget config.
from research_agent.context import trim_history


def test_config_has_max_history_tokens(temp_data_dir):
    from research_agent.config import get_context_config
    assert get_context_config()["max_history_tokens"] == 24000


def test_trim_noop_under_budget():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
    assert trim_history(msgs, 10_000) == msgs


def test_trim_zero_means_unlimited():
    msgs = [{"role": "user", "content": "x" * 5000}]
    assert trim_history(msgs, 0) == msgs


def test_trim_keeps_system_and_user_drops_old():
    big = "x" * 2000
    msgs = [{"role": "system", "content": big},
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": big},
            {"role": "tool", "content": big, "tool_call_id": "1"},
            {"role": "assistant", "content": big, "tool_calls": [{"id": "2"}]},
            {"role": "tool", "content": big, "tool_call_id": "2"}]
    out = trim_history(msgs, 100)          # only system + user fit
    assert out[0]["role"] == "system"
    nonsys = [m for m in out if m["role"] != "system"]
    assert nonsys and nonsys[0]["role"] == "user"   # never an orphan leading tool
    assert len(out) < len(msgs)


def test_trim_keeps_recent_tail_no_orphan_tool():
    small = "y" * 40
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": small, "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": small, "tool_call_id": "1"},
            {"role": "assistant", "content": small},
            {"role": "tool", "content": small, "tool_call_id": "2"}]
    out = trim_history(msgs, 200)
    # if any tool survived, the kept tail must not START with a tool message
    nonsys = [m for m in out if m["role"] != "system"]
    assert nonsys and nonsys[0]["role"] == "user"
