"""Conversation persistence (CORE).

Keeps the research_agent.memory conversation-turn API that agent.py and older
code depend on (re-exported from project_manager). Tier B semantic memory was
removed; automatic task memory is now episodic (`research_agent.plan`).
"""
from research_agent.project_manager import save_turn
from research_agent.models import ConversationTurn


def store_turn(workspace_dir: str, chat_id: str, round_number: int,
               user_msg: str, assistant_msg: str, sections: list[dict] | None = None,
               execution_id: str | None = None):
    save_turn(workspace_dir, chat_id, round_number, user_msg, assistant_msg, sections,
              execution_id=execution_id)


def get_recent_turns(workspace_dir: str, chat_id: str,
                     limit: int = 10) -> list[ConversationTurn]:
    from research_agent.project_manager import get_recent_turns as pm_turns
    raw_turns = pm_turns(workspace_dir, chat_id, limit)
    return [_dict_to_turn(t) for t in raw_turns]


def count_uncompressed_turns(workspace_dir: str, chat_id: str) -> int:
    from research_agent.project_manager import count_uncompressed_turns as pm_count
    return pm_count(workspace_dir, chat_id)


def mark_compressed(workspace_dir: str, chat_id: str,
                    turn_indices: list[int], summary: str):
    from research_agent.project_manager import mark_compressed as pm_mark
    pm_mark(workspace_dir, chat_id, turn_indices, summary)


def _dict_to_turn(d: dict) -> ConversationTurn:
    return ConversationTurn(
        id=str(d.get("round", "")),
        round_number=d.get("round", 0),
        user_message=d.get("user", ""),
        assistant_message=d.get("assistant", ""),
        compressed=bool(d.get("compressed", False)),
        summary=d.get("summary", ""),
        timestamp=d.get("timestamp", ""),
    )
