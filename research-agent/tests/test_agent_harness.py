from research_agent.llm import MockLLMProvider
from research_agent.agent import run_agent, parse_action
from research_agent.models import AgentState, Action
from research_agent.store import init_db


def test_parse_action_json():
    a = parse_action('{"action": "retrieve", "query": "test"}')
    assert a.action == "retrieve"
    assert a.query == "test"


def test_parse_action_bad_json():
    a = parse_action("not json")
    assert a.action == "generate"


def test_parse_action_plain_text():
    a = parse_action("这是一个普通回答")
    assert a.action == "generate"


def test_agent_generates_with_mock_llm(temp_data_dir):
    init_db()
    llm = MockLLMProvider(["这是一个回答"])
    state = AgentState(user_input="hello")
    result = run_agent("hello", llm, state)
    assert result.final_response == "这是一个回答"


def test_agent_retrieves_then_generates(temp_data_dir):
    init_db()
    llm = MockLLMProvider([
        '{"action": "retrieve", "query": "test query"}',
        "基于检索结果生成的回答",
        "基于检索结果生成的回答",
        "基于检索结果生成的回答",
    ])
    state = AgentState(user_input="查询")
    result = run_agent("查询", llm, state)
    assert result.final_response == "基于检索结果生成的回答"


def test_agent_guardrail_blocks(temp_data_dir):
    init_db()
    llm = MockLLMProvider(['{"action": "shell", "query": "rm -rf /"}'])
    state = AgentState(user_input="danger")
    result = run_agent("danger", llm, state)
    assert "拦截" in result.final_response


def test_agent_stops_after_max_rounds(temp_data_dir):
    init_db()
    llm = MockLLMProvider(['{"action": "retrieve", "query": "x"}'] * 20)
    state = AgentState(user_input="loop")
    result = run_agent("loop", llm, state)
    assert result.final_response != ""


def test_agent_skill_trigger(temp_data_dir):
    init_db()
    from research_agent.store import insert_project
    from research_agent.models import Project, ProjectStatus
    insert_project(Project(id="p1", topic="test", status=ProjectStatus.ACTIVE))

    from unittest.mock import patch
    mock_response = [{
        "paper_id": "abc", "title": "Test Paper", "year": 2024,
        "citation_count": 10, "authors": ["Author"],
        "doi": "10.000/test", "abstract": "An abstract.",
        "venue": "Test Journal",
    }]
    with patch("research_agent.skills.paper_search.search_papers", return_value=mock_response):
        llm = MockLLMProvider(["should not be called"])
        state = AgentState(user_input="搜索论文 transformer")
        result = run_agent("搜索论文 transformer", llm, state)
        assert result.final_response != ""