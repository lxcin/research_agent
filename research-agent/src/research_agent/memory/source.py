"""Extraction source construction for Tier B memory.

Guarantees the material fed to the small-model extractor NEVER contains tool
calls / tool results / file diffs / shell output. Only conversational content
(user messages + assistant answers) and project note increments are allowed.
"""
import re

from research_agent.memory import get_recent_turns, count_uncompressed_turns

# Sections/turns that carry tool traces are identified by these markers.
_TOOL_MARKERS = (
    "[工具结果", "Tool Results", "tool_call", "tool_result", "stdout", "stderr",
    "=== 工具调用结果 ===", "[自动验证]", "Command failed", "returncode",
)


def _is_tool_line(text: str) -> bool:
    low = text.lower()
    return any(m.lower() in low for m in _TOOL_MARKERS)


def _clean_text_block(text: str) -> str:
    """Drop any line that resembles tool output from an assistant block."""
    kept = []
    for line in (text or "").splitlines():
        if _is_tool_line(line):
            continue
        if not line.strip():
            continue
        kept.append(line.strip())
    return "\n".join(kept)


def _clean_conversation_block(turns) -> str:
    """Serialize recent turns, dropping any line that resembles tool output."""
    lines: list[str] = []
    for t in turns:
        user = (t.user_message or "").strip()
        assistant = (t.assistant_message or "").strip()
        if user:
            lines.append(f"用户: {user}")
        if assistant:
            cleaned = _clean_text_block(assistant)
            if cleaned:
                lines.append(f"助手: {cleaned}")
    return "\n".join(lines)


def build_extraction_source(workspace_dir: str, chat_id: str,
                            user_input: str = "", final_response: str = "",
                            limit: int = 6) -> dict:
    """Assemble the extraction source (conversation-only) for one trigger.

    Returns {'conversation': str, 'notes': str, 'has_content': bool}.
    The returned text is guaranteed tool-call-free by construction.
    """
    notes = ""
    try:
        from research_agent import project_manager as pm
        progress = pm.load_progress(workspace_dir)
        if progress:
            # Only note-like lines (not tool diffs) in the tail.
            entries = [ln for ln in progress.strip().splitlines()
                       if ln.strip() and not ln.startswith("<!--")]
            notes = "\n".join(entries[-20:])
    except Exception:
        notes = ""

    parts: list[str] = []
    try:
        turns = get_recent_turns(workspace_dir, chat_id, limit=limit)
        conv = _clean_conversation_block(turns)
        if conv.strip():
            parts.append(conv)
    except Exception:
        pass

    # Fresh user/assistant for the current turn if not yet persisted.
    if user_input:
        parts.append(f"用户: {user_input.strip()}")
    if final_response:
        cleaned_resp = _clean_text_block(final_response)
        if cleaned_resp:
            parts.append(f"助手: {cleaned_resp}")

    conversation = "\n".join(p for p in parts if p.strip())
    return {
        "conversation": conversation,
        "notes": notes,
        "has_content": bool(conversation.strip()),
    }
