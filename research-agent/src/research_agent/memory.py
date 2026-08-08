"""Conversation storage: persists turns via project_manager."""
from research_agent.models import ConversationTurn
from research_agent import project_manager


def store_turn(workspace_dir: str, chat_id: str, round_number: int,
               user_msg: str, assistant_msg: str, sections: list[dict] | None = None):
    project_manager.save_turn(workspace_dir, chat_id, round_number,
                              user_msg, assistant_msg, sections)


def get_recent_turns(workspace_dir: str, chat_id: str,
                     limit: int = 10) -> list[ConversationTurn]:
    raw_turns = project_manager.get_recent_turns(workspace_dir, chat_id, limit)
    return [_dict_to_turn(t) for t in raw_turns]



def count_uncompressed_turns(workspace_dir: str, chat_id: str) -> int:
    return project_manager.count_uncompressed_turns(workspace_dir, chat_id)


def mark_compressed(workspace_dir: str, chat_id: str,
                    turn_indices: list[int], summary: str):
    project_manager.mark_compressed(workspace_dir, chat_id, turn_indices, summary)


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
