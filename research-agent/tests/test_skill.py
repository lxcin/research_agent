from research_agent.skill import load_skills, find_skill, Skill


def test_load_skills():
    skills = load_skills()
    assert len(skills) >= 3
    names = {s.name for s in skills}
    assert "paper-search" in names
    assert "literature-review" in names
    assert "write-report" in names


def test_find_skill_match():
    skills = load_skills()
    skill = find_skill("帮我搜索论文关于transformer的相关文献", skills)
    assert skill is not None
    assert skill.name == "paper-search"


def test_find_skill_no_match():
    skills = load_skills()
    assert find_skill("今天天气怎么样", skills) is None


def test_paper_search_skill_handler(temp_data_dir):
    from unittest.mock import patch, MagicMock
    from research_agent.models import AgentState
    from research_agent.skills.paper_search import _execute_paper_search
    from research_agent.store import init_db

    init_db()
    mock_response = [{
        "paper_id": "abc", "title": "Test Paper", "year": 2024,
        "citation_count": 10, "authors": ["Author"],
        "doi": "10.000/test", "abstract": "An abstract."
    }]
    with patch("research_agent.skills.paper_search.search_papers", return_value=mock_response):
        state = AgentState(user_input="搜索论文 transformer attention")
        result = _execute_paper_search(state)
        assert "Test Paper" in result.final_response