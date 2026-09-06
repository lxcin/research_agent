# tests/test_polish.py — Phase F polish verification
import os

from research_agent.llm import MockLLMProvider
from research_agent.models import AgentState, Project, ProjectStatus, PendingTask
from research_agent.agent import _maybe_compress, _detect_pending_task, _persist_pending_task
from research_agent.memory.tier_b import get_manager, MemoryScope, MemoryKind


# ── F.2 token-budget compression ────────────────────────────────────────────

def test_compress_skips_small_history(temp_data_dir, temp_workspace):
    ws, chat_id = temp_workspace
    from research_agent.memory import store_turn
    for i in range(8):
        store_turn(ws, chat_id, i + 1, f"短消息{i}", "ok")
    llm = MockLLMProvider(['{"conclusions":"a","dead_ends":"b"}', "进度"])
    _maybe_compress(ws, chat_id, llm)
    # tiny history stays under budget → no compression call happened
    assert llm.call_count == 0


def test_compress_fires_on_large_history(temp_data_dir, temp_workspace, monkeypatch):
    ws, chat_id = temp_workspace
    from research_agent.memory import store_turn, count_uncompressed_turns
    long_msg = "这是一段比较长的对话内容。" * 300  # enough tokens
    for i in range(10):
        store_turn(ws, chat_id, i + 1, long_msg, "助手回复。" * 100)
    monkeypatch.setenv("RESEARCH_AGENT_COMPRESS_TOKENS", "2000")
    llm = MockLLMProvider([
        '{"conclusions": "结论X", "dead_ends": "死路Y"}',
        "写完了综述",
    ])
    _maybe_compress(ws, chat_id, llm)
    assert llm.call_count >= 1
    assert count_uncompressed_turns(ws, chat_id) < 10


def test_compress_keeps_recent_turns(temp_data_dir, temp_workspace, monkeypatch):
    ws, chat_id = temp_workspace
    from research_agent.memory import store_turn, get_recent_turns
    long_msg = "内容较长的轮次。" * 400
    for i in range(10):
        store_turn(ws, chat_id, i + 1, long_msg, "回复" * 200)
    monkeypatch.setenv("RESEARCH_AGENT_COMPRESS_TOKENS", "3000")
    llm = MockLLMProvider(['{"conclusions":"c","dead_ends":"d"}', "p"])
    _maybe_compress(ws, chat_id, llm)
    recent = [t for t in get_recent_turns(ws, chat_id) if not t.compressed]
    # recent 5 turns kept uncompressed
    assert len(recent) <= 5


# ── F.3 structured pending task ─────────────────────────────────────────────

def test_detect_pending_task_trims_description():
    t = _detect_pending_task("综述写完了。现在需要你手动跑一遍实验来验证结果，然后我们看数据。")
    assert t is not None
    assert "手动跑一遍实验" in t.description
    assert t.description.startswith("需要你")
    # no long raw tail
    assert len(t.description) < 100


def test_detect_no_pending_when_done():
    assert _detect_pending_task("已完成，HPLC 数据已经全部跑完。") is None


def test_persist_pending_task_writes_tier_b(temp_data_dir):
    state = AgentState(user_input="x")
    state.active_project = Project(id="proj1", topic="t",
                                   status=ProjectStatus.WAITING)
    task = PendingTask(description="需要你手动复现实验", expected_time="2-3h")
    _persist_pending_task(state, task)
    mgr = get_manager()
    units = mgr.list_units(scope=MemoryScope.USER, kind=MemoryKind.TASK)
    assert any("手动复现" in u.text for u in units)


def test_persist_pending_task_skips_short(temp_data_dir):
    state = AgentState(user_input="x")
    _persist_pending_task(state, PendingTask(description="好"))
    assert get_manager().count() == 0


def test_persist_pending_task_disabled_by_config(temp_data_dir, monkeypatch):
    import research_agent.config as cfg
    monkeypatch.setattr(cfg, "get_memory_config", lambda: {"enabled": False})
    state = AgentState(user_input="x")
    _persist_pending_task(state, PendingTask(description="需要你做个任务"))
    assert get_manager().count() == 0
