from research_agent.router import _tokenize, _compute_match_score, route_to_project
from research_agent.retrieval import _tokenize_query, _tokenize_doc
from research_agent.models import Project, ProjectStatus


def test_tokenize_chinese():
    tokens = _tokenize("我想研究Transformer模型在NLP中的应用")
    assert "Transformer" in tokens
    assert "NLP" in tokens
    assert "研究" in tokens


def test_tokenize_english():
    tokens = _tokenize("I want to study Transformer")
    assert "transformer" in tokens


def test_route_chinese():
    p1 = Project(id="p1", topic="Transformer模型研究", status=ProjectStatus.ACTIVE)
    p2 = Project(id="p2", topic="HPLC化合物分析", status=ProjectStatus.ACTIVE)
    result = route_to_project("上次那个Transformer的attention机制分析结果怎么样了", [p1, p2])
    assert result is not None
    assert result.id == "p1"


def test_tokenize_query_chinese():
    tokens = _tokenize_query("Transformer注意力机制")
    assert "Transformer" in tokens
    assert "注意力" in tokens


def test_tokenize_doc_chinese():
    tokens = _tokenize_doc("Transformer模型使用自注意力机制")
    assert "Transformer" in tokens
    assert "注意力" in tokens