"""Token-aware context builder for the agent harness."""
import tiktoken
from research_agent.config import get_max_context_tokens
from research_agent.models import AgentState, ConversationTurn

BASE_SYSTEM_PROMPT = """You are PaperPilot, a general personal assistant.

你是 PaperPilot，一个通用个人助手。直接做事，不要解释过程，不要长篇计划。最终回复格式：简短结果总结 + 下一步建议（可选）。

回复规则：
- 文件创建/编辑成功后：只说"已创建 xxx"或"已修改 xxx"，不要重复输出文件内容
- 工具调用失败时：简短说明失败原因和建议
- 涉及用户本人的问题（偏好/说过的事/领域/历史决定）：调用 search_memory 查询长期记忆后再回答，不要凭空编造
- 用户明确要求记住某信息时：调用 memorize
- 最终回复永远不要包含道歉、"我可以帮你"、自我评价之类的话
- 不要在第一句说"正在xxx..."——直接给出结果

工具规则：
- file_write 成功后不要用 shell_exec 验证，除非用户要求"""


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

    # 2. Tool capabilities — injected later by agent after intent routing
    # (pass tool_names to inject filtered capabilities)

    # 3. Project context from workspace
    ws = getattr(state, 'workspace_dir', '')
    if ws:
        from research_agent import project_manager as pm
        proj_topic = state.active_project.topic if state.active_project else ws
        proj = f"Project: {proj_topic} / 当前项目: {proj_topic}"
        progress = pm.load_progress(ws)
        if progress:
            entries = progress.strip().split("\n")
            recent = entries[-20:]
            proj += f"\n项目进展:\n" + "\n".join(recent)
        messages.append({"role": "system", "content": proj})
    elif state.active_project:
        proj = f"Project: {state.active_project.topic} / 当前项目: {state.active_project.topic}"
        notes = getattr(state.active_project, 'progress_text', '')
        if notes:
            entries = notes.strip().split("\n")
            recent = entries[-10:]
            proj += f"\n研究笔记({len(entries)}条):\n" + "\n".join(recent)
        messages.append({"role": "system", "content": proj})

    # 4. Personal-memory meta hint (NOT memory content — content only comes via
    #    the search_memory tool which the LLM invokes itself, agentic RAG read).
    try:
        from research_agent.tools import is_plugin_enabled as is_enabled
        if is_enabled("memory"):
            messages.append({"role": "system", "content":
                "关于用户本人的问题（偏好/说过的事/领域/历史决定），调用 search_memory 查询长期记忆后再回答；"
                "用户明确要求记住某信息时调用 memorize。不要凭空编造用户记忆。"})
    except Exception:
        pass

    # 5. Conversation history
    if hasattr(state, 'conversation_turns') and state.conversation_turns:
        compressed = [t for t in state.conversation_turns if t.compressed and t.summary]
        recent = [t for t in state.conversation_turns if not t.compressed][-10:]
        if compressed:
            conclusions_parts = []
            dead_ends_parts = []
            for t in compressed:
                try:
                    import json
                    d = json.loads(t.summary)
                    if d.get("conclusions"):
                        conclusions_parts.append(d["conclusions"])
                    if d.get("dead_ends"):
                        dead_ends_parts.append(d["dead_ends"])
                except (json.JSONDecodeError, TypeError):
                    conclusions_parts.append(t.summary)  # legacy plain text
            if conclusions_parts:
                messages.append({"role": "system", "content": "历史摘要/结论:\n" + "\n".join(conclusions_parts)})
            if dead_ends_parts:
                messages.append({"role": "system", "content": "已验证不可行的方向(避免重复):\n" + "\n".join(dead_ends_parts)})
        if recent:
            messages.append({"role": "system", "content": "最近对话:\n" + format_turns(recent)})

    # 5. External skills (YAML .md files) injected as system message before user input
    user_lower = state.user_input.lower()
    from research_agent.skill_loader import load_skills_from_dir, get_active_skills_context
    import os
    skills_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "skills")
    external_skills = load_skills_from_dir(skills_dir)
    skill_ctx = get_active_skills_context(external_skills, user_lower)
    if skill_ctx:
        messages.append({"role": "system", "content": skill_ctx})

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
