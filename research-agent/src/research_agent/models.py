"""Core data models for research-agent."""
from dataclasses import dataclass, field
from enum import Enum
from research_agent.state_components import ComponentStore


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
class AgentState:
    """Dynamic field container. Field declarations are injected, not class members.

    AgentState() injects the compatibility bundle. Pass fields=() for an empty
    container, or a custom declaration bundle. Attribute access remains compatible.
    """
    def __init__(self, *, fields=None, **values):
        from research_agent.state_defaults import DEFAULT_STATE_FIELDS
        object.__setattr__(self, "components", ComponentStore())
        for spec in DEFAULT_STATE_FIELDS if fields is None else fields:
            self.components.inject(spec)
        for key, value in values.items():
            if key not in self.components.keys():
                raise TypeError(f"Unknown state field: {key}")
            self.components.set(key, value)

    def __getattr__(self, name):
        store = self.__dict__.get("components")
        if store is None:
            raise AttributeError(name)
        try:
            return store.get(name)
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        if name in self.components.keys():
            self.components.set(name, value)
        elif name.startswith("_"):
            # Legacy host scratch attributes; migrate to declarations separately.
            object.__setattr__(self, name, value)
        else:
            raise AttributeError(f"State field must be injected before assignment: {name}")

    def __copy__(self):
        import copy
        result = object.__new__(type(self))
        result.__dict__.update(self.__dict__)
        store = copy.copy(self.components)
        with self.components._lock:
            store._values = {
                key: spec.fork(value) if spec.fork is not None else
                     (dict(value) if key == "caps" else copy.deepcopy(value))
                for key, value in self.components._values.items()
                for spec in (self.components._specs[key],)
            }
            store._specs = self.components._specs.copy()
            store._revisions = self.components._revisions.copy()
            for key, value in store._values.items():
                store._specs[key].check(value)
        from threading import RLock
        store._lock = RLock()
        object.__setattr__(result, "components", store)
        return result


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
