"""Core data models for research-agent."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    WAITING = "waiting"
    PAUSED = "paused"
    DONE = "done"


class Confidence(str, Enum):
    CERTAIN = "certain"
    SPECULATIVE = "speculative"
    UNCERTAIN = "uncertain"


@dataclass
class Paper:
    id: str | None = None
    title: str = ""
    doi: str = ""
    year: int = 0
    source_score: int = 5
    citation_count: int = 0
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    file_path: str = ""

@dataclass
class Chunk:
    id: str | None = None
    paper_id: str = ""
    text: str = ""
    chunk_index: int = 0

@dataclass
class PendingTask:
    description: str = ""
    expected_format: str = ""
    expected_time: str = ""


@dataclass
class PlanStep:
    step: str = ""
    owner: str = "agent"
    status: str = "pending"
    depends_on: list[str] = field(default_factory=list)


@dataclass
class Pitfall:
    phenomenon: str = ""
    root_cause: str = ""
    solution: str = ""
    improvement: str = ""


@dataclass
class AccumulatedWisdom:
    sops: list[str] = field(default_factory=list)
    pitfalls: list[dict] = field(default_factory=list)  # [{phenomenon, root_cause, solution, improvement}]
    frameworks: list[str] = field(default_factory=list)
    agent_improvements: list[str] = field(default_factory=list)


@dataclass
class Project:
    id: str | None = None
    topic: str = ""
    status: ProjectStatus = ProjectStatus.ACTIVE
    history_summary: str = ""
    accumulated_wisdom: AccumulatedWisdom = field(default_factory=AccumulatedWisdom)
    intro_summary: str = ""
    plan: list[PlanStep] = field(default_factory=list)
    pending_task: PendingTask | None = None
    created_at: str = ""
    updated_at: str = ""
@dataclass
class AgentState:
    """LangGraph state schema. Memory lives in LangGraph checkpoint per thread_id (project_id)."""
    user_input: str = ""
    active_project: Project | None = None
    all_projects: list[Project] = field(default_factory=list)
    retrieved_chunks: list[dict] = field(default_factory=list)
    retrieved_context: list[dict] = field(default_factory=list)
    retrieval_sufficient: bool = False
    retry_count: int = 0
    final_response: str = ""
    error: str = ""
    citations: list[str] = field(default_factory=list)
    confidence: str = Confidence.UNCERTAIN.value
    search_query: str = ""
    needs_retrieval: bool = True
    needs_compression: bool = False
    conversation_turns: list = field(default_factory=list)
    compressed_summaries: list[str] = field(default_factory=list)
    round_count: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class ConversationTurn:
    id: str | None = None
    project_id: str = ""
    round_number: int = 0
    user_message: str = ""
    assistant_message: str = ""
    timestamp: str = ""
    compressed: bool = False
    summary: str = ""


@dataclass
class Action:
    action: str = "generate"
    query: str = ""
    target: str = "papers"
    reasoning: str = ""