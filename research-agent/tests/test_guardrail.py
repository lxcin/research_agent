from research_agent.guardrail import guardrail
from research_agent.models import Action


def test_blocks_dangerous_action():
    assert guardrail(Action(action="shell", query="rm -rf /")) is not None


def test_blocks_dangerous_query():
    assert guardrail(Action(action="retrieve", query="drop table papers")) is not None


def test_allows_normal_action():
    assert guardrail(Action(action="retrieve", query="transformer attention")) is None


def test_allows_generate():
    assert guardrail(Action(action="generate")) is None