"""Core data models for research-agent."""
from dataclasses import dataclass, field
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
class PendingTask:
    description: str = ""
    expected_format: str = ""
    expected_time: str = ""


@dataclass
class Project:
    id: str | None = None
    topic: str = ""
    status: ProjectStatus = ProjectStatus.ACTIVE
    progress_text: str = ""
    pending_task: PendingTask | None = None
    created_at: str = ""
    updated_at: str = ""
    workspace_dir: str = ""
@dataclass
class AgentState:
    """Agent execution state. workspace_dir and active_chat_id supersede the old project routing."""
    user_input: str = ""
    workspace_dir: str = ""
    active_chat_id: str = ""
    active_project: Project | None = None
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
    sections: list[dict] = field(default_factory=list)


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