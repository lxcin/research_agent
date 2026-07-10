from research_agent.llm import MockLLMProvider


def test_mock_llm_returns_sequence():
    llm = MockLLMProvider(["hello", "world"])
    assert llm.complete([]) == "hello"
    assert llm.complete([]) == "world"
    assert llm.call_count == 2


def test_mock_llm_wraps_around():
    llm = MockLLMProvider(["one"])
    assert llm.complete([]) == "one"
    assert llm.complete([]) == "one"