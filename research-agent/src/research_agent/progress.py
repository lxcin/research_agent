"""Project review: summarize progress from conversation history."""
from datetime import datetime
from research_agent.models import Project
from research_agent.llm import LLMProvider
from research_agent.memory import get_all_turns, get_recent_turns
from research_agent.store import update_project


def review_project(project: Project, llm: LLMProvider) -> Project:
    turns = get_all_turns(project.id)
    if not turns:
        return project

    turns_text = "\n".join([
        f"用户: {t.user_message}\n助手: {t.assistant_message[:200]}"
        for t in turns[-10:]
    ])

    summary = llm.complete(
        [{"role": "user", "content": f"基于以下对话，用一句话总结项目当前进展，包括关键发现、待办事项:\n{turns_text}"}],
        max_tokens=100
    )

    project.history_summary = summary
    project.updated_at = datetime.now().isoformat()
    update_project(project)
    return project