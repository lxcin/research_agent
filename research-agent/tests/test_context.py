from research_agent.context import count_tokens, build_context
from research_agent.models import AgentState


def test_count_tokens():
    assert count_tokens("hello world") > 0


def test_build_context_minimal():
    state = AgentState(user_input="test")
    messages = build_context(state)
    assert len(messages) >= 2


def test_build_context_with_project():
    from research_agent.models import Project, ProjectStatus
    state = AgentState(
        user_input="test",
        active_project=Project(id="p1", topic="Test", status=ProjectStatus.ACTIVE),
    )
    messages = build_context(state)
    assert any("Test" in m.get("content", "") for m in messages)