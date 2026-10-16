"""Token-aware context builder for the agent harness."""
import tiktoken
from research_agent.config import get_max_context_tokens
from research_agent.models import AgentState, ConversationTurn

BASE_SYSTEM_PROMPT = """You are PaperPilot, a general personal assistant.

你是 PaperPilot，一个通用个人助手。直接做事，不要解释过程，不要长篇计划。最终回复格式：简短结果总结 + 下一步建议（可选）。

回复规则：
- 汇报操作结果时，以工具确认的完成状态和影响范围为准；不要重复粘贴大段内容
- 工具调用失败时：简短说明失败原因和建议
- 最终回复永远不要包含道歉、"我可以帮你"、自我评价之类的话
- 不要在第一句说"正在xxx..."——直接给出结果
"""


def count_tokens(text: str) -> int:
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text) // 4


def user_content_with_attachments(state):
    """Build the user message content for this turn.

    - no attachments → plain string;
    - text-only attachments → merge question + file texts into ONE string
      (leaner and more provider-compatible than a text-only block list);
    - any image → multimodal block list (question text + text/image blocks).
    """
    blocks = getattr(state, "attachment_blocks", None) or []
    text = getattr(state, "user_input", "") or ""
    if not blocks:
        return text
    has_image = any(isinstance(b, dict) and b.get("type") == "image_url" for b in blocks)
    if not has_image:
        parts = [text] + [b.get("text", "") for b in blocks
                          if isinstance(b, dict) and b.get("type") == "text"]
        return "\n\n".join(p for p in parts if p)
    return [{"type": "text", "text": text}, *blocks]


def _content_text(content) -> str:
    """Flatten message content (str, or multimodal block list) to text for counting."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                if c.get("type") == "text":
                    parts.append(str(c.get("text", "")))
                elif c.get("type") == "image_url":
                    parts.append("[image]")
        return " ".join(parts)
    return str(content or "")


def build_context(state: AgentState, registry=None, model_name: str = "") -> list[dict]:
    max_tokens = get_max_context_tokens(model_name)
    messages = []

    # 1. Identity
    messages.append({"role": "system", "content": BASE_SYSTEM_PROMPT})

    # 1b. Runtime brief — where/what/how (derived at runtime, not hardcoded)
    try:
        from research_agent.brief import build_runtime_brief
        brief = build_runtime_brief(state, getattr(state, "workspace_dir", ""))
        if brief:
            messages.append({"role": "system", "content": brief})
    except Exception:
        pass

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
    from research_agent.skill_loader import (
        load_skills_from_dir, get_active_skills_context, matched_skills)
    import os
    skills_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "skills")
    external_skills = load_skills_from_dir(skills_dir)
    # user (self-evolved) skills live under data_dir/skills — load them too
    try:
        from research_agent.config import get_data_dir
        user_skills_dir = str(get_data_dir() / "skills")
        external_skills = external_skills + load_skills_from_dir(user_skills_dir)
    except Exception:
        pass
    skill_ctx = get_active_skills_context(external_skills, user_lower)
    if skill_ctx:
        messages.append({"role": "system", "content": skill_ctx})
        # evaluation closed-loop: track which skills were actually used (per trace)
        try:
            from research_agent import evolve
            from research_agent.trace_log import get_trace_id
            for s in matched_skills(external_skills, user_lower):
                evolve.note_skill_used(s.name, trace=get_trace_id())
        except Exception:
            pass

    # 7. User input LAST — freshest in context (attachments merged/attached)
    messages.append({"role": "user", "content": user_content_with_attachments(state)})

    return trim_messages(messages, max_tokens)


def format_turns(turns: list[ConversationTurn]) -> str:
    lines = []
    for t in turns:
        lines.append(f"用户: {t.user_message}")
        if t.assistant_message:
            lines.append(f"助手: {t.assistant_message}")
    return "\n".join(lines)



def trim_messages(messages: list[dict], max_tokens: int) -> list[dict]:
    """Compatibility entry: preserve text; host budgets after tool injection."""
    return list(messages)
