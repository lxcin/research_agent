# tests/test_context_compression.py — window-ratio compression budget + switch
from research_agent.agent import _compress_budget, _maybe_compress
from research_agent.llm import MockLLMProvider


def test_budget_env_override_wins(monkeypatch):
    monkeypatch.setenv("RESEARCH_AGENT_COMPRESS_TOKENS", "1234")
    assert _compress_budget() == 1234


def test_budget_derived_from_window_ratio(monkeypatch):
    monkeypatch.delenv("RESEARCH_AGENT_COMPRESS_TOKENS", raising=False)
    monkeypatch.setattr("research_agent.config.get_context_config",
                        lambda: {"compress_ratio": 0.5, "compress_enabled": True})
    monkeypatch.setattr("research_agent.config.get_max_context_tokens",
                        lambda name="": 20000)
    monkeypatch.setattr("research_agent.config.get_model_name", lambda: "x")
    assert _compress_budget() == 10000


def test_budget_has_floor(monkeypatch):
    monkeypatch.delenv("RESEARCH_AGENT_COMPRESS_TOKENS", raising=False)
    monkeypatch.setattr("research_agent.config.get_context_config",
                        lambda: {"compress_ratio": 0.6})
    monkeypatch.setattr("research_agent.config.get_max_context_tokens",
                        lambda name="": 100)  # 100*0.6 = 60 < floor
    monkeypatch.setattr("research_agent.config.get_model_name", lambda: "x")
    assert _compress_budget() == 2000


def test_budget_unknown_window_disables_compression(monkeypatch):
    monkeypatch.delenv("RESEARCH_AGENT_COMPRESS_TOKENS", raising=False)
    monkeypatch.setattr("research_agent.config.get_max_context_tokens",
                        lambda name="": 0)
    assert _compress_budget() >= 10 ** 9


def test_maybe_compress_disabled_skips_even_large_history(temp_data_dir, temp_workspace, monkeypatch):
    ws, chat_id = temp_workspace
    from research_agent.memory import store_turn
    long_msg = "内容较长的轮次。" * 400
    for i in range(10):
        store_turn(ws, chat_id, i + 1, long_msg, "回复" * 200)
    monkeypatch.setenv("RESEARCH_AGENT_COMPRESS_TOKENS", "2000")
    monkeypatch.setattr("research_agent.config.get_context_config",
                        lambda: {"compress_enabled": False})
    llm = MockLLMProvider(['{"conclusions":"a","dead_ends":"b"}', "p"])
    _maybe_compress(ws, chat_id, llm)
    assert llm.call_count == 0
