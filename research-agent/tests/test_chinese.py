from research_agent.router import extract_project_topic


def test_extract_topic_chinese():
    topic = extract_project_topic("帮我开个新项目研究Transformer模型")
    assert "Transformer" in topic


def test_extract_topic_with_create_keyword():
    topic = extract_project_topic("create project about HPLC analysis")
    assert "HPLC" in topic
    assert "create project" not in topic.lower()


def test_extract_topic_defaults_to_input():
    topic = extract_project_topic("帮我搜一下注意力机制的论文")
    assert "注意力" in topic
