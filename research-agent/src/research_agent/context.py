"""Token-aware context builder for the agent harness."""
import tiktoken
from research_agent.config import get_max_context_tokens
from research_agent.models import AgentState, ConversationTurn

SYSTEM_PROMPT = """你是一个科研助手。你可以：
- 检索论文知识库回答用户问题
- 管理项目进度
- 总结对话

当用户提到论文相关内容时，你应该输出 JSON 决定下一步动作：
{"action": "retrieve", "query": "搜索关键词", "target": "papers"}
{"action": "generate", "reasoning": "为什么不需要检索"}
{"action": "stop", "reasoning": "为什么结束"}

如果没有需要检索的内容，直接回答用户问题即可。"""


def count_tokens(text: str) -> int:
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text) // 4


def build_context(state: AgentState) -> list[dict]:
    max_tokens = get_max_context_tokens()
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if state.active_project:
        proj = f"当前项目: {state.active_project.topic}"
        if state.active_project.history_summary:
            proj += f"\n项目摘要: {state.active_project.history_summary}"
        messages.append({"role": "system", "content": proj})

    if hasattr(state, 'compressed_summaries') and state.compressed_summaries:
        summaries = "\n".join(state.compressed_summaries)
        messages.append({"role": "system", "content": f"早期对话摘要:\n{summaries}"})

    if hasattr(state, 'conversation_turns') and state.conversation_turns:
        turns_text = format_turns(state.conversation_turns[-10:])
        messages.append({"role": "system", "content": f"最近对话:\n{turns_text}"})

    if state.retrieved_context:
        ctx_text = format_retrieved(state.retrieved_context)
        messages.append({"role": "system", "content": f"检索结果:\n{ctx_text}"})

    messages.append({"role": "user", "content": state.user_input})

    return trim_messages(messages, max_tokens)


def format_turns(turns: list[ConversationTurn]) -> str:
    lines = []
    for t in turns:
        lines.append(f"用户: {t.user_message}")
        if t.assistant_message:
            lines.append(f"助手: {t.assistant_message}")
    return "\n".join(lines)


def format_retrieved(items: list) -> str:
    lines = []
    for i, item in enumerate(items[:5]):
        text = item.get("text", "") if isinstance(item, dict) else str(item)
        source = item.get("source", "unknown") if isinstance(item, dict) else "unknown"
        lines.append(f"[{i+1}] ({source}) {text[:300]}")
    return "\n".join(lines)


def trim_messages(messages: list[dict], max_tokens: int) -> list[dict]:
    total = sum(count_tokens(m.get("content", "")) for m in messages)
    if total <= max_tokens:
        return messages

    result = []
    for m in messages:
        result.append(m)
        if count_tokens(m.get("content", "")) > max_tokens // 2:
            m["content"] = m["content"][:max_tokens // 2 * 4] + "..."
    return result