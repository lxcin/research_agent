"""Periodic compression: extract accumulated wisdom from project dialogue."""
import json
import litellm

from research_agent.models import AgentState, Project, AccumulatedWisdom, ProjectStatus
from research_agent.store import get_project, update_project


def should_compress(state: AgentState) -> bool:
    return state.needs_compression


def extract_wisdom(dialogue: str) -> AccumulatedWisdom:
    """Extract structured knowledge from dialogue text using LLM."""
    prompt = f"""Analyze this research dialogue and extract structured knowledge. Output ONLY JSON with these fields:
- sops: list of standard operating procedures discovered
- pitfalls: list of {{phenomenon, root_cause, solution, improvement}}
- frameworks: list of thinking frameworks or troubleshooting approaches
- agent_improvements: list of ways the AI agent should change its future behavior

If a field has no content, use empty list [].

Dialogue:
{dialogue[:8000]}
"""
    try:
        response = litellm.completion(
            model="claude-3-haiku-20240307",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.1,
        )
        data = json.loads(response.choices[0].message.content.strip())
        return AccumulatedWisdom(
            sops=data.get("sops", []),
            pitfalls=data.get("pitfalls", []),
            frameworks=data.get("frameworks", []),
            agent_improvements=data.get("agent_improvements", []),
        )
    except Exception:
        return AccumulatedWisdom()


def compress(state: AgentState) -> Project:
    """Compress project dialogue into updated Project with accumulated wisdom.
    Called when should_compress() is True (>=40 rounds, user triggers, or token budget low).

    Args:
        state: current AgentState with active_project and LangGraph messages
    Returns:
        Updated Project with fresh history_summary and accumulated_wisdom
    """
    project = state.active_project
    if not project:
        return Project()

    dialogue_summary = "Conversation context not available."
    try:
        state_dict = state.__dict__ if hasattr(state, '__dict__') else {}
        messages = state_dict.get("messages", [])
        if hasattr(state, "messages"):
            messages = state.messages
        if messages:
            dialogue_summary = "\n".join(
                str(m.content) if hasattr(m, "content") else str(m)
                for m in messages[-40:]
            )[:10000]
    except Exception:
        pass

    prompt = f"""You are a research assistant performing compression. Given the project dialogue, output a JSON with:
- history_summary: 1-2 sentences summarizing current state
- intro_summary: 1 sentence from agent's perspective ("I'm helping with...")
- sops: extracted SOPs (list of strings)
- pitfalls: extracted issues with solutions (list of {{phenomenon, root_cause, solution, improvement}})
- frameworks: thinking frameworks learned (list of strings)
- agent_improvements: how to improve future behavior (list of strings)

Dialogue:
{dialogue_summary[:8000]}
"""
    response = litellm.completion(
        model="claude-3-haiku-20240307",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=600,
        temperature=0.1,
    )
    try:
        data = json.loads(response.choices[0].message.content.strip())
    except json.JSONDecodeError:
        data = {}

    new_wisdom = AccumulatedWisdom(
        sops=data.get("sops", []),
        pitfalls=data.get("pitfalls", []),
        frameworks=data.get("frameworks", []),
        agent_improvements=data.get("agent_improvements", []),
    )

    project.history_summary = data.get("history_summary", project.history_summary)
    project.intro_summary = data.get("intro_summary", project.intro_summary)
    project.accumulated_wisdom = new_wisdom
    update_project(project)

    return project