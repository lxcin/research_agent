"""Token-aware context builder for the agent harness."""
import tiktoken
from research_agent.config import get_max_context_tokens
from research_agent.models import AgentState, ConversationTurn

BASE_SYSTEM_PROMPT = """You are PaperPilot, a research assistant. Think before acting. Base all conclusions on data or run results. Use update_notes to record findings. Be honest about limitations.

你是 PaperPilot，一个研究助手。思考后行动。结论基于数据或运行结果。随时用 update_notes 记录发现。诚实面对能力边界，不编造结果。Always match the user's language in your response."""


SURVEY_WORKFLOW = """## Survey Writing Protocol / 综述写作流程
1. retrieve/search_papers to find candidate papers / 查找候选论文
2. BEFORE reading, check title and abstract relevance / 读前检查标题和摘要是否相关
3. read_paper on relevant papers. Returns: title, authors, year, full_text.
4. After reading, write survey citing with [N] format / 读完写综述，用 [N] 引用
5. Reference list: [N] Title. Authors. Year. / 参考文献格式"""


def count_tokens(text: str) -> int:
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text) // 4


def build_context(state: AgentState, registry=None, model_name: str = "") -> list[dict]:
    max_tokens = get_max_context_tokens(model_name)
    messages = []

    # 1. Identity
    messages.append({"role": "system", "content": BASE_SYSTEM_PROMPT})

    # 2. Tool capabilities
    if registry:
        messages.append({"role": "system", "content": registry.generate_capabilities()})

    # 3. Project context + notes
    if state.active_project:
        proj = f"Project: {state.active_project.topic} / 当前项目: {state.active_project.topic}"
        notes = getattr(state.active_project.accumulated_wisdom, 'notes', '')
        if notes:
            entries = notes.strip().split("\n")
            recent = entries[-10:]
            proj += f"\n研究笔记({len(entries)}条):\n" + "\n".join(recent)
        if state.active_project.history_summary:
            proj += f"\n项目摘要: {state.active_project.history_summary}"
        messages.append({"role": "system", "content": proj})

        # Cross-project reference
        from research_agent.store import get_all_projects as gap
        all_projects = gap()
        for p in all_projects:
            if p.id == state.active_project.id: continue
            if p.topic.lower() in state.user_input.lower():
                p_notes = getattr(p.accumulated_wisdom, 'notes', '')
                if p_notes:
                    entries = p_notes.strip().split("\n")
                    messages.append({"role": "system", "content": f"项目「{p.topic}」笔记:\n" + "\n".join(entries[-5:])})
                break

    # 4. Conversation history
    if hasattr(state, 'conversation_turns') and state.conversation_turns:
        summaries = [t.summary for t in state.conversation_turns if t.compressed and t.summary]
        recent = [t for t in state.conversation_turns if not t.compressed][-10:]
        if summaries:
            messages.append({"role": "system", "content": "历史摘要:\n" + "\n".join(summaries)})
        if recent:
            messages.append({"role": "system", "content": "最近对话:\n" + format_turns(recent)})

    # 5. Retrieved context
    if state.retrieved_context:
        messages.append({"role": "system", "content": "检索结果:\n" + format_retrieved(state.retrieved_context)})

    # 6. Skill / Workflow (injected as system message before user input)
    user_lower = state.user_input.lower()
    if any(kw in user_lower for kw in ["survey", "综述", "review", "文献调研"]):
        messages.append({"role": "system", "content": SURVEY_WORKFLOW})

    # 7. User input LAST — freshest in context
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