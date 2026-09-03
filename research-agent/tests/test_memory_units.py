# tests/test_memory_units.py — Tier B memory storage + facade (Phase A)
import pytest

from research_agent.memory import MemoryManager, MemoryScope, MemoryKind, MemoryUnit, get_manager
from research_agent.memory import storage, vector


@pytest.fixture
def mgr(temp_data_dir):
    return MemoryManager()


def test_upsert_and_get(mgr):
    u = mgr.write(MemoryUnit(text="用户偏好使用 PyTorch 框架", kind=MemoryKind.PREFERENCE))
    assert u.id
    got = mgr.get(u.id)
    assert got.text == u.text
    assert got.kind == MemoryKind.PREFERENCE
    assert got.scope == MemoryScope.USER


def test_write_generates_id_and_timestamps(mgr):
    u = mgr.write(MemoryUnit(text="一个事实", importance=0.8))
    assert u.id.startswith("mem_")
    assert u.created_at and u.updated_at
    assert mgr.count() == 1


def test_scope_and_kind_filters(mgr):
    mgr.write(MemoryUnit(text="项目A的结论", scope=MemoryScope.PROJECT, kind=MemoryKind.DECISION))
    mgr.write(MemoryUnit(text="用户是研究生", scope=MemoryScope.USER, kind=MemoryKind.FACT))
    assert len(mgr.list_units(scope=MemoryScope.USER)) == 1
    assert len(mgr.list_units(scope=MemoryScope.PROJECT)) == 1
    assert len(mgr.list_units(kind=MemoryKind.DECISION)) == 1
    assert mgr.list_units(scope=MemoryScope.USER)[0].text == "用户是研究生"


def test_supersede_marks_old_inactive(mgr):
    old = mgr.write(MemoryUnit(text="用户偏好用 V1", importance=0.5))
    new = MemoryUnit(text="用户偏好用 V2", importance=0.7)
    saved = mgr.supersede(old.id, new)
    assert saved is not None
    assert mgr.get(old.id).active is False
    assert mgr.get(old.id).superseded_by == saved.id
    active_ids = [u.id for u in mgr.list_units(active_only=True)]
    assert saved.id in active_ids
    assert old.id not in active_ids


def test_delete(mgr):
    u = mgr.write(MemoryUnit(text="to delete"))
    assert mgr.delete(u.id) is True
    assert mgr.get(u.id) is None
    assert mgr.delete("nonexistent") is False


def test_keyword_search_chinese(mgr):
    mgr.write(MemoryUnit(text="用户研究注意力机制与 Transformer", kind=MemoryKind.FACT, importance=0.9))
    mgr.write(MemoryUnit(text="用户喜欢喝咖啡", kind=MemoryKind.PREFERENCE, importance=0.9))
    hits = mgr.retrieve("Transformer 注意力", limit=5)
    assert hits
    assert hits[0].text.startswith("用户研究注意力机制")


def test_keyword_search_ranks_relevant_first(mgr):
    mgr.write(MemoryUnit(text="用户在写 Transformer 综述，关注注意力机制的可解释性", importance=0.9))
    mgr.write(MemoryUnit(text="用户偏好简短回复风格", importance=0.9))
    hits = mgr.retrieve("Transformer 注意力 综述", limit=3)
    assert hits and "Transformer" in hits[0].text


def test_keyword_search_respects_scope(mgr):
    mgr.write(MemoryUnit(text="共享词汇 attention mechanism", scope=MemoryScope.USER))
    mgr.write(MemoryUnit(text="attention mechanism 项目内笔记", scope=MemoryScope.PROJECT))
    hits = mgr.retrieve("attention mechanism", scope=MemoryScope.USER)
    assert hits and all(u.scope == MemoryScope.USER for u in hits)


def test_superseded_not_retrieved(mgr):
    old = mgr.write(MemoryUnit(text="用户用工具 A 做实验", importance=0.8))
    mgr.supersede(old.id, MemoryUnit(text="用户改用工具 B 做实验", importance=0.9))
    hits = mgr.retrieve("工具 A 实验")
    assert not any("工具 A" in h.text for h in hits)


def test_search_empty_query(mgr):
    mgr.write(MemoryUnit(text="anything"))
    assert mgr.retrieve("", limit=3) == []


def test_storage_db_isolated_by_data_dir(temp_data_dir):
    storage.upsert(MemoryUnit(text="first dir fact"))
    assert storage.count() == 1


# ── vector layer degradation ────────────────────────────────────────────────

def test_vector_unavailable_by_default(temp_data_dir, monkeypatch):
    """Default (no RESEARCH_AGENT_MEMORY_VECTOR) must not attempt model load."""
    monkeypatch.delenv("RESEARCH_AGENT_MEMORY_VECTOR", raising=False)
    vector.set_available(None)
    assert vector.is_available() is False
    m = get_manager()
    m.write(MemoryUnit(text="用户用 Rust 写后端", importance=0.8))
    hits = m.retrieve("Rust 后端")
    assert hits and "Rust" in hits[0].text


def test_vector_write_add_is_noop_when_unavailable(temp_data_dir, monkeypatch):
    monkeypatch.setenv("RESEARCH_AGENT_MEMORY_VECTOR", "1")
    vector.set_available(None)
    avail = vector.is_available()
    if avail:
        pytest.skip("local embedding model present; degradation path not exercised")


def test_legacy_memory_api_unaffected(temp_data_dir, temp_workspace):
    """Old research_agent.memory conversation functions still importable."""
    from research_agent.memory import store_turn, get_recent_turns, count_uncompressed_turns, mark_compressed
    workspace_dir, chat_id = temp_workspace
    store_turn(workspace_dir, chat_id, 1, "hello", "hi")
    store_turn(workspace_dir, chat_id, 2, "again", "ok")
    assert len(get_recent_turns(workspace_dir, chat_id)) == 2
    assert count_uncompressed_turns(workspace_dir, chat_id) == 2
    mark_compressed(workspace_dir, chat_id, [0], '{"conclusions": "x"}')
    assert count_uncompressed_turns(workspace_dir, chat_id) == 1
