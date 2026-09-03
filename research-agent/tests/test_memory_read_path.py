# tests/test_memory_read_path.py — Phase C: route/retrieve/trim/context injection
import pytest

from research_agent.llm import MockLLMProvider
from research_agent.memory.models import MemoryKind, MemoryScope, MemoryUnit
from research_agent.memory import get_manager
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


# ── C.1 route ───────────────────────────────────────────────────────────────

def test_route_recognizes_personal_queries():
    assert mem_retrieve.route("你还记得我上次说的那个想法吗")
    assert mem_retrieve.route("我的偏好是什么")
    assert mem_retrieve.route("谁记得我导师姓什么") is False  # not a memory ask
    assert mem_retrieve.route("请检索Transformer注意力机制的论文") is False


def test_route_empty():
    assert mem_retrieve.route("") is False


def test_route_llm_fallback(monkeypatch):
    llm = MockLLMProvider(["yes"])
    assert mem_retrieve.route_llm(llm, "你还记得我偏好吗") is True
    llm2 = MockLLMProvider(["no"])
    assert mem_retrieve.route_llm(llm2, "找几篇论文") is False


# ── C.2 retrieve ────────────────────────────────────────────────────────────

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


# ── C.3 format/trim budget ──────────────────────────────────────────────────

def test_format_block_has_wrappers(seeded_user_memory):
    units = mem_retrieve.retrieve("用户研究什么方向", scope=MemoryScope.USER)
    block = mem_retrieve.format_block(units)
    assert block.startswith("<Global Memory")
    assert block.rstrip().endswith("</Global Memory>")
    assert "[fact]" in block or "[preference]" in block


def test_format_block_empty():
    assert mem_retrieve.format_block([]) == ""


def test_format_block_respects_budget(seeded_user_memory):
    units = mem_retrieve.retrieve("用户", scope=MemoryScope.USER, limit=10)
    block = mem_retrieve.format_block(units, max_tokens=60)
    # small budget must not blow out
    from research_agent.context import count_tokens
    assert count_tokens(block) <= 120


def test_format_block_never_includes_tool_content(seeded_user_memory):
    units = [MemoryUnit(text="user statement", kind=MemoryKind.FACT)]
    block = mem_retrieve.format_block(units)
    assert "stdout" not in block and "tool" not in block.lower()


# ── C.4 context injection ───────────────────────────────────────────────────

def test_build_context_injects_memory_when_triggered(seeded_user_memory):
    from research_agent.context import build_context
    from research_agent.models import AgentState
    state = AgentState(user_input="你还记得我研究什么方向吗")
    messages = build_context(state)
    contents = [m.get("content", "") for m in messages]
    assert any("<Global Memory" in c for c in contents)
    assert any("Transformer" in c for c in contents)
    assert len(state.memory_units) > 0


def test_build_context_skips_memory_for_normal_query(seeded_user_memory):
    from research_agent.context import build_context
    from research_agent.models import AgentState
    state = AgentState(user_input="帮我检索几篇注意力机制综述")
    messages = build_context(state)
    assert not any("<Global Memory" in m.get("content", "") for m in messages)


def test_build_context_disabled_by_config(seeded_user_memory, monkeypatch):
    import research_agent.config as cfg
    monkeypatch.setattr(cfg, "get_memory_config",
                        lambda: {"enabled": False, "max_inject_tokens": 1500})
    from research_agent.context import build_context
    from research_agent.models import AgentState
    state = AgentState(user_input="你还记得我研究什么方向吗")
    messages = build_context(state)
    assert not any("<Global Memory" in m.get("content", "") for m in messages)


# ── C.4/5 end-to-end block + state.memory_units ─────────────────────────────

def test_memory_block_end_to_end(seeded_user_memory):
    block = mem_retrieve.build_memory_block("我记得你说过我导师姓王")
    assert "王" in block
