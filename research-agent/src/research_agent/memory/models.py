"""Tier B memory data model: MemoryUnit (atomic, typed, traceable)."""
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone


class MemoryScope(str, Enum):
    USER = "user"          # cross-project personal memory
    PROJECT = "project"    # scoped to one project but shared across chats
    PAPER = "paper"        # paper-related notes (rare; papers live in Tier A)


class MemoryKind(str, Enum):
    FACT = "fact"            # durable user fact
    PREFERENCE = "preference"  # user preference / style
    DECISION = "decision"    # a decision made
    TASK = "task"            # commitment / to-do / pending item
    DEAD_END = "dead_end"    # tried-but-failed / pitfalls (avoid repeat)
    INSIGHT = "insight"      # cross-project synthesis
    REFERENCE = "reference"  # pointer to an external thing/user said
    STYLE = "style"          # writing/tooling style requirement


KIND_LABEL = {k.name: k.value for k in MemoryKind}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class MemoryUnit:
    text: str
    scope: MemoryScope = MemoryScope.USER
    kind: MemoryKind = MemoryKind.FACT
    importance: float = 0.5
    id: str = ""
    source: dict = field(default_factory=dict)   # {project_id, chat_id, round, tool?}
    created_at: str = ""
    updated_at: str = ""
    superseded_by: str = ""   # id of newer unit that replaces this one
    embedding_id: str = ""    # chroma id (when vector layer available)
    score: float = 0.0        # fill-in from retrieve (not persisted)
    active: bool = True       # superseded units become inactive

    def __post_init__(self):
        now = utcnow()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "scope": self.scope.value,
            "kind": self.kind.value,
            "text": self.text,
            "importance": self.importance,
            "source": __import__("json").dumps(self.source, ensure_ascii=False),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "superseded_by": self.superseded_by,
            "embedding_id": self.embedding_id,
            "active": 1 if self.active else 0,
        }


def row_to_unit(row) -> MemoryUnit:
    import json
    try:
        source = json.loads(row["source"]) if row["source"] else {}
    except (TypeError, ValueError):
        source = {}
    return MemoryUnit(
        id=row["id"],
        scope=MemoryScope(row["scope"]),
        kind=MemoryKind(row["kind"]),
        text=row["text"],
        importance=row["importance"],
        source=source,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        superseded_by=row["superseded_by"] or "",
        embedding_id=row["embedding_id"] or "",
        active=bool(row["active"]),
    )
