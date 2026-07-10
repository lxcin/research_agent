"""Skill system: load, match, and execute pluggable skills."""
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


def load_skills() -> list[Skill]:
    return []


def find_skill(user_input: str, skills: list[Skill]) -> Skill | None:
    input_lower = user_input.lower()
    for skill in skills:
        for phrase in skill.trigger_phrases:
            if phrase in input_lower:
                return skill
    return None