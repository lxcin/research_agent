"""Skill system: load, match, and execute pluggable skills with MCP tool support."""
from dataclasses import dataclass, field
from typing import Callable

from research_agent.models import AgentState


@dataclass
class Skill:
    name: str
    description: str
    trigger_phrases: list[str]
    system_prompt: str = ""
    mcp_tools: list[str] = field(default_factory=list)
    handler: Callable[[AgentState], AgentState] | None = None


def _builtin_skills() -> list[Skill]:
    from research_agent.skills.paper_search import paper_search_skill
    from research_agent.skills.literature_review import literature_review_skill
    from research_agent.skills.write_report import write_report_skill
    return [paper_search_skill, literature_review_skill, write_report_skill]


def load_skills() -> list[Skill]:
    return _builtin_skills()


def find_skill(user_input: str, skills: list[Skill]) -> Skill | None:
    input_lower = user_input.lower()
    for skill in skills:
        for phrase in skill.trigger_phrases:
            if phrase in input_lower:
                return skill
    return None