# tests/test_memory_recall_guard.py — per-turn cap, dedup, confidence signaling
import types

import pytest

from research_agent.memory.models import MemoryUnit, MemoryKind, MemoryScope
from research_agent.tools.builtin import memory_tool as mt


class _FakeMgr:
    def __init__(self, units):
        self.units = units

    def retrieve(self, query, scope=None, kind=None, limit=5):
        return self.units[:limit]


def _state(**kw):
    return types.SimpleNamespace(**kw)


def _run(params, state, units=None, vector_on=True, monkeypatch=None):
    monkeypatch.setattr("research_agent.memory.tier_b.get_manager",
                        lambda: _FakeMgr(units or []))
    monkeypatch.setattr("research_agent.memory.vector.is_available", lambda: vector_on)
    return mt._handle_search_memory(params, None, state, lambda e, d: None)


def test_cap_blocks_after_max_retrievals(temp_data_dir, monkeypatch):
    st = _state(_memory_retrievals=["a", "b", "c", "d"])
    res = _run({"query": "任意"}, st, monkeypatch=monkeypatch)
    assert res.data.get("terminal") is True and res.data["found"] == 0


def test_dedup_rejects_near_duplicate_query(temp_data_dir, monkeypatch):
    st = _state(_memory_retrievals=["用户偏好用什么框架"])
    res = _run({"query": "用户 偏好用什么框架"}, st, monkeypatch=monkeypatch)
    assert res.data.get("duplicate") is True and res.data["found"] == 0


def test_distinct_queries_are_allowed(temp_data_dir, monkeypatch):
    st = _state(_memory_retrievals=["用户偏好"])
    u = MemoryUnit(text="x", kind=MemoryKind.FACT, scope=MemoryScope.USER)
    res = _run({"query": "用户用什么分词工具"}, st, units=[u], monkeypatch=monkeypatch)
    assert "duplicate" not in res.data


@pytest.mark.parametrize("score,expected", [(0.9, "strong"), (0.55, "weak"), (0.4, "none")])
def test_confidence_buckets(score, expected, temp_data_dir, monkeypatch):
    u = MemoryUnit(text="x", kind=MemoryKind.FACT, scope=MemoryScope.USER)
    u.score = score
    res = _run({"query": "q"}, _state(), units=[u], monkeypatch=monkeypatch)
    assert res.data["confidence"] == expected


def test_confidence_unknown_without_vector_layer(temp_data_dir, monkeypatch):
    u = MemoryUnit(text="x", kind=MemoryKind.FACT, scope=MemoryScope.USER)
    u.score = 0.9
    res = _run({"query": "q"}, _state(), units=[u], vector_on=False, monkeypatch=monkeypatch)
    assert res.data["confidence"] == "unknown"


def test_no_hits_reports_none_confidence(temp_data_dir, monkeypatch):
    res = _run({"query": "q"}, _state(), units=[], monkeypatch=monkeypatch)
    assert res.data["found"] == 0 and res.data["confidence"] == "none"
    assert res.data.get("guidance")
