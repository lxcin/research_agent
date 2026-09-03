# tests/test_memory_write_path.py — Phase B: source/extract/verify/pipeline/memorize
import json

from research_agent.llm import MockLLMProvider
from research_agent.memory.models import MemoryKind, MemoryScope
from research_agent.memory import source as mem_source
from research_agent.memory import extractor, pipeline
from research_agent.memory import get_manager, storage
from research_agent.tools.builtin.memory_tool import _handle_memorize


# ── B.1 source construction filters tool content ────────────────────────────

def test_source_uses_only_conversation(temp_workspace):
    ws, chat_id = temp_workspace
    from research_agent.memory import store_turn
    store_turn(ws, chat_id, 1, "我在用PyTorch写实验", "好的")
    # a turn whose assistant answer contains a tool dump must be stripped
    store_turn(ws, chat_id, 2,
               "运行一下脚本",
               "=== 工具调用结果 ===\nstdout: 0.95\nstderr: 返回码0")
    snap = mem_source.build_extraction_source(ws, chat_id)
    assert snap["has_content"] is True
    assert "工具调用结果" not in snap["conversation"]
    assert "stdout" not in snap["conversation"]
    assert "PyTorch" in snap["conversation"]


def test_source_requires_content(temp_data_dir, temp_workspace):
    ws, chat_id = temp_workspace
    snap = mem_source.build_extraction_source(ws, chat_id)
    # empty conversation but notes may exist; has_content refers to conversation
    assert "conversation" in snap


# ── B.2 EXTRACT ─────────────────────────────────────────────────────────────

def test_extract_valid_json():
    llm = MockLLMProvider([json.dumps([
        {"kind": "preference", "text": "用户偏好使用PyTorch框架", "importance": 0.8},
        {"kind": "fact", "text": "用户是自然语言处理方向的研究生", "importance": 0.9},
    ], ensure_ascii=False)])
    units = extractor.extract_units(llm, "用户：我用PyTorch\n助手：好的", scope=MemoryScope.USER)
    assert len(units) == 2
    assert units[0].kind == MemoryKind.PREFERENCE
    assert units[1].kind == MemoryKind.FACT
    assert units[0].importance == 0.8


def test_extract_fenced_json():
    llm = MockLLMProvider(["```json\n[{\"kind\": \"fact\", \"text\": \"用户读博\", \"importance\": 0.6}]\n```"])
    units = extractor.extract_units(llm, "对话")
    assert len(units) == 1 and units[0].text == "用户读博"


def test_extract_filters_invalid_kind_and_bad_importance():
    llm = MockLLMProvider([json.dumps([
        {"kind": "preference", "text": "用户偏好中文回复", "importance": 0.7},
        {"kind": "bogus_kind", "text": "不应入库", "importance": 0.5},
        {"kind": "preference", "text": "坏importance", "importance": "oops"},
    ])])
    units = extractor.extract_units(llm, "对话")
    assert len(units) == 2
    assert units[1].importance == 0.5  # bad importance → default


def test_extract_garbage_returns_empty():
    llm = MockLLMProvider(["not json at all"])
    assert extractor.extract_units(llm, "对话") == []


def test_extract_empty_conversation():
    llm = MockLLMProvider(["[]"])
    assert extractor.extract_units(llm, "") == []


# ── B.3 VERIFY ──────────────────────────────────────────────────────────────

def test_verify_drops_exact_duplicate(temp_data_dir):
    storage.upsert(MemoryUnitFactory("用户偏好使用PyTorch", kind=MemoryKind.PREFERENCE))
    new = [MemoryUnitFactory("用户偏好使用PyTorch", kind=MemoryKind.PREFERENCE)]
    keep = extractor.verify_new_units(MockLLMProvider(["unrelated"]), new, MemoryScope.USER)
    assert keep == []


def test_verify_near_duplicate_llm_says_duplicate(temp_data_dir):
    storage.upsert(MemoryUnitFactory("用户偏好使用PyTorch来训练模型"))
    llm = MockLLMProvider(["duplicate"])
    keep = extractor.verify_new_units(llm,
                                      [MemoryUnitFactory("用户喜欢用PyTorch训练模型")],
                                      MemoryScope.USER)
    assert keep == []


def test_verify_opposite_kept_both(temp_data_dir):
    storage.upsert(MemoryUnitFactory("用户偏好每天工作八小时"))
    llm = MockLLMProvider(["opposite"])
    new = [MemoryUnitFactory("用户偏好每天工作十小时")]
    keep = extractor.verify_new_units(llm, new, MemoryScope.USER)
    assert len(keep) == 1


def test_verify_different_kept(temp_data_dir):
    storage.upsert(MemoryUnitFactory("用户研究注意力机制"))
    llm = MockLLMProvider(["unrelated"])
    new = [MemoryUnitFactory("用户喜欢喝美式咖啡")]
    keep = extractor.verify_new_units(llm, new, MemoryScope.USER)
    assert len(keep) == 1


# ── distill end-to-end (EXTRACT + VERIFY) ───────────────────────────────────

def test_distill_no_tool_content_captured(temp_data_dir):
    storage.reset_db_for_tests()
    # simulate extractor emitting one unit then a duplicate detection call
    llm = MockLLMProvider([
        json.dumps([{"kind": "preference", "text": "用户偏好简洁回复", "importance": 0.8}], ensure_ascii=False),
        "unrelated",
    ])
    units = extractor.distill(llm, "用户说：回复简短点", scope=MemoryScope.USER)
    assert len(units) == 1
    assert "简洁" in units[0].text
    # verify prompt never received tool content
    joined = " ".join(str(c["messages"]) for c in llm.calls)
    assert "工具结果" not in joined


# ── B.4 pipeline (async) ────────────────────────────────────────────────────

def test_pipeline_submit_and_process(temp_data_dir, monkeypatch):
    monkeypatch.setenv("RESEARCH_AGENT_MEMORY_VECTOR", "0")
    from research_agent.memory import pipeline as pl
    from research_agent.llm import MockLLMProvider as Mock
    llm = Mock([json.dumps(
        [{"kind": "fact", "text": "用户是论文作者陆欣", "importance": 0.9}],
        ensure_ascii=False)])
    pl.submit("用户：我是陆欣，在做这个项目", llm)
    pl.drain(timeout=5)
    assert get_manager().count() >= 1


def test_pipeline_disabled_by_config(temp_data_dir, monkeypatch):
    import research_agent.config as cfg
    monkeypatch.setattr(cfg, "load_config", lambda: {"memory": {"enabled": False}})
    from research_agent.memory import pipeline as pl
    from research_agent.llm import MockLLMProvider as Mock
    llm = Mock(["[]"])
    pl.submit("用户：记得我", llm)
    pl.drain(timeout=5)
    assert get_manager().count() == 0


# ── B.6 memorize tool ───────────────────────────────────────────────────────

def test_memorize_writes_and_rejects_bad_kind(temp_data_dir):
    from research_agent.models import AgentState
    from research_agent.tools.builtin.memory_tool import memorize_tool
    state = AgentState(user_input="x")
    result = _handle_memorize({"text": "用户偏好每封邮件用中文签名", "kind": "style"},
                              None, state, lambda et, d: None)
    assert result.success
    assert get_manager().count() == 1
    bad = _handle_memorize({"text": "x", "kind": "nope"}, None, state, lambda et, d: None)
    assert not bad.success


def test_memorize_tool_registered_and_schema(temp_data_dir):
    from research_agent.tools import get_registry
    from research_agent.tools.builtin import register_builtins
    register_builtins()
    registry = get_registry()
    assert "memorize" in registry.tools
    schema = registry.list_for_llm()
    assert any(t["function"]["name"] == "memorize" for t in schema)


# helper
def MemoryUnitFactory(text, kind=MemoryKind.FACT, importance=0.6):
    from research_agent.memory.models import MemoryUnit
    return MemoryUnit(text=text, kind=kind, importance=importance,
                      scope=MemoryScope.USER, source={"test": True})
