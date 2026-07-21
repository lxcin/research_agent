"""Skill system: pluggable research skills registered as tools."""
from dataclasses import dataclass, field
from typing import Callable

from research_agent.models import AgentState


@dataclass
class Skill:
    name: str
    description: str
    trigger_phrases: list[str]
    system_prompt: str = ""
    handler: Callable[[AgentState], AgentState] | None = None


def load_skills() -> list[Skill]:
    return []


def load_and_register_skills():
    """Register skills as tools. Currently using context injection instead."""
    pass


def find_skill(user_input: str, skills: list[Skill]) -> Skill | None:
    return None