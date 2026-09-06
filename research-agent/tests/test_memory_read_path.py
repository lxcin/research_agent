# tests/test_memory_read_path.py — Tier B read path via search_memory tool
import pytest

from research_agent.memory.models import MemoryKind, MemoryScope, MemoryUnit
from research_agent.memory.tier_b import get_manager
from research_agent.memory import retrieve as mem_retrieve


@pytest.fixture
def seeded_user_memory(temp_data_dir):
    mgr = get_manager()
    mgr.write(MemoryUnit(text="用户偏好用中文写邮件并用中文签名", kind=MemoryKind.PREFERENCE,
                         importance=0.8, source={"project_id": "projA"}))
    mgr.write(MemoryUnit(text="用户研究注意力机制与Transformer可解释性", kind=MemoryKind.FACT,
                         importance=0.9, source={"project_id": "projA", "chat_id": "chatX"}))
    mgr.write(MemoryUnit(text="用户导师姓王，在北理工", kind=MemoryKind.FACT, importance=0.7))
    mgr.write(MemoryUnit(text="用户做过HPLC实验但失败了，原因是柱子污染", kind=MemoryKind.DEAD_END,
                         importance=0.6))
    return mgr


# ── retrieve helper ─────────────────────────────────────────────────────────

def test_retrieve_returns_user_scope(seeded_user_memory):
    hits = mem_retrieve.retrieve("用户偏好的写作风格", scope=MemoryScope.USER)
    assert hits
    assert all(u.scope == MemoryScope.USER for u in hits)
    assert any("签名" in h.text for h in hits)


def test_retrieve_not_cross_pollutes(seeded_user_memory):
    # project-scoped memories must not leak when querying user scope
    mgr = get_manager()
    mgr.write(MemoryUnit(text="项目内部使用的私有代号XYZ", scope=MemoryScope.PROJECT,
                         kind=MemoryKind.REFERENCE))
    hits = mem_retrieve.retrieve("私有代号 XYZ 是什么", scope=MemoryScope.USER)
    assert not any("XYZ" in h.text for h in hits)


def test_retrieve_empty_query(seeded_user_memory):
    assert mem_retrieve.retrieve("", scope=MemoryScope.USER) == []


# ── format_hits ─────────────────────────────────────────────────────────────

def test_format_hits_readable(seeded_user_memory):
    units = mem_retrieve.retrieve("用户研究什么方向", scope=MemoryScope.USER)
    block = mem_retrieve.format_hits(units)
    assert block.startswith("长期记忆命中")
    assert "Transformer" in block
    assert "[fact]" in block


def test_format_hits_empty():
    assert mem_retrieve.format_hits([]) == ""


def test_format_hits_never_includes_tool_content():
    units = [MemoryUnit(text="user statement", kind=MemoryKind.FACT)]
    block = mem_retrieve.format_hits(units)
    assert "stdout" not in block and "tool" not in block.lower()


# ── search_memory tool (agentic read; no auto content injection) ────────────

def _mk_state():
    from research_agent.models import AgentState
    return AgentState(user_input="x")


def _mock_emit(et, d):
    pass


def test_build_context_only_hints_no_content(seeded_user_memory):
    """Context must NOT inject memory content; only a static meta hint."""
    from research_agent.context import build_context
    from research_agent.models import AgentState
    state = AgentState(user_input="你还记得我研究什么方向吗")
    messages = build_context(state)
    contents = [m.get("content", "") for m in messages]
    # no memory content auto-injected
    assert not any("<Global Memory" in c for c in contents)
    assert not any("Transformer" in c for c in contents)
    # but a discoverability hint referencing the tool is present
    assert any("search_memory" in c for c in contents)


def test_search_memory_tool_returns_hits(seeded_user_memory):
    from research_agent.tools.builtin.memory_tool import _handle_search_memory
    state = _mk_state()
    result = _handle_search_memory({"query": "用户研究什么方向"}, None, state, _mock_emit)
    assert result.success
    assert result.data["found"] >= 1
    assert result.data["_formatted"]
    assert any("Transformer" in h["text"] for h in result.data["hits"])


def test_search_memory_tool_empty(seeded_user_memory):
    from research_agent.tools.builtin.memory_tool import _handle_search_memory
    state = _mk_state()
    result = _handle_search_memory({"query": "完全不存在的关键词xyzzy"}, None, state, _mock_emit)
    assert result.success
    assert result.data["found"] == 0


def test_search_memory_tool_missing_query():
    from research_agent.tools.builtin.memory_tool import _handle_search_memory
    state = _mk_state()
    result = _handle_search_memory({}, None, state, _mock_emit)
    assert not result.success


def test_search_memory_tool_kind_filter(seeded_user_memory):
    from research_agent.tools.builtin.memory_tool import _handle_search_memory
    state = _mk_state()
    result = _handle_search_memory({"query": "用户", "kind": "preference"}, None, state, _mock_emit)
    assert result.success
    assert all(h["kind"] == "preference" for h in result.data["hits"])


def test_search_memory_tool_registered(seeded_user_memory):
    from research_agent.tools import get_registry
    from research_agent.tools.builtin import register_builtins
    register_builtins()
    registry = get_registry()
    assert "search_memory" in registry.tools
    schema = registry.list_for_llm()
    assert any(t["function"]["name"] == "search_memory" for t in schema)
