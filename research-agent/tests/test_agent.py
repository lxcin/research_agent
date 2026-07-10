from unittest.mock import patch, MagicMock
from research_agent.agent import run_agent, parse_action, AgentState
from research_agent.llm import MockLLMProvider
from research_agent.models import Project, ProjectStatus


def test_run_agent_with_project_routing(temp_data_dir):
    from research_agent.store import init_db, insert_project

    init_db()
    insert_project(Project(id="p1", topic="HPLC compound screening", status=ProjectStatus.ACTIVE,
                           history_summary="We discussed HPLC purity analysis."))

    llm = MockLLMProvider(["HPLC 分析结果表明纯度达到98%"])
    state = AgentState(user_input="上次那个 HPLC 结果拿到了")
    result = run_agent(state.user_input, llm, state)
    assert result.active_project is not None
    assert result.active_project.id == "p1"
    assert result.final_response is not None


def test_run_agent_creates_new_project(temp_data_dir):
    from research_agent.store import init_db
    init_db()

    llm = MockLLMProvider(["MOF 材料的气体吸附性能研究取得进展"])
    state = AgentState(user_input="我想开一个新项目，研究MOF材料的气体吸附性能")
    result = run_agent(state.user_input, llm, state)
    assert result.active_project is not None


def test_parse_action_malformed():
    a = parse_action('{"bad json')
    assert a.action == "generate"


def test_parse_action_empty():
    a = parse_action("")
    assert a.action == "generate"