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
    """Agent execution state.

    `caps` is the per-capability namespace: each plugin/feature stores its own
    private state under state.caps["<capability_id>"] and must NOT write top-level
    fields. Fields below marked [legacy paper] are being migrated into caps and
    kept only for backward compatibility during the paper-plugin refactor.
    """
    user_input: str = ""
    workspace_dir: str = ""
    active_chat_id: str = ""
    active_project: Project | None = None

    # Per-capability private state (plugin namespace). e.g. caps["paper"], caps["memory"].
    caps: dict = field(default_factory=dict)

    # [legacy paper] → migrating to caps["paper"]
    retrieved_chunks: list[dict] = field(default_factory=list)
    retrieved_context: list[dict] = field(default_factory=list)
    retrieval_sufficient: bool = False
    search_query: str = ""
    needs_retrieval: bool = True
    citations: list[str] = field(default_factory=list)
    confidence: str = Confidence.UNCERTAIN.value

    # Kernel fields
    retry_count: int = 0
    final_response: str = ""
    error: str = ""
    needs_compression: bool = False
    conversation_turns: list = field(default_factory=list)
    compressed_summaries: list[str] = field(default_factory=list)
    memory_units: list = field(default_factory=list)
    round_count: int = 0
    errors: list[str] = field(default_factory=list)
    sections: list[dict] = field(default_factory=list)
    _pending_confirms: dict = field(default_factory=dict)


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