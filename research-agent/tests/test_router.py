from research_agent.router import route_to_project, should_create_project, extract_project_topic
from research_agent.models import Project, ProjectStatus


def test_route_exact_keyword_match():
    projects = [
        Project(id="1", topic="HPLC analysis of compound screening"),
        Project(id="2", topic="literature review on peptide drugs"),
    ]
    result = route_to_project("上次那个 HPLC 结果拿到了", projects)
    assert result is not None
    assert result.id == "1"


def test_route_partial_topic_match():
    projects = [
        Project(id="a", topic="molecular dynamics simulation of protein folding"),
        Project(id="b", topic="deep learning for retrosynthesis"),
    ]
    result = route_to_project("我想聊聊protein folding的进展", projects)
    assert result is not None
    assert result.id == "a"


def test_route_no_match():
    projects = [
        Project(id="x", topic="catalyst design"),
    ]
    result = route_to_project("帮我写个Python脚本处理CSV", projects)
    assert result is None


def test_should_create_project():
    projects = [Project(id="1", topic="catalyst design")]
    assert should_create_project("全新的量子化学计算项目", projects) is True
    assert should_create_project("再聊聊catalyst的事", projects) is False


def test_extract_project_topic():
    topic = extract_project_topic("我想开一个新项目，研究MOF材料的气体吸附性能")
    assert len(topic) > 3
    assert "MOF" in topic or "吸附" in topic