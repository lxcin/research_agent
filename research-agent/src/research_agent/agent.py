"""Self-implemented agent main loop."""
import json
from datetime import datetime

from research_agent.models import AgentState, Action, Project, ProjectStatus, ConversationTurn, PendingTask
from research_agent.llm import LLMProvider
from research_agent.context import build_context
from research_agent.guardrail import guardrail
from research_agent.retrieval import hybrid_search, build_bm25_index
from research_agent.memory import store_turn, get_recent_turns, count_uncompressed_turns, mark_compressed
from research_agent.store import init_db, get_all_projects, insert_project, update_project
from research_agent.router import route_to_project, extract_project_topic
from research_agent.skill import load_skills, find_skill
from research_agent.validate import validate_response

MAX_ROUNDS = 5


def _build_resume_message(project: Project) -> str:
    """Build resume context when user returns to a WAITING project."""
    parts = [f"欢迎回来！项目「{project.topic}」之前处于等待状态。"]
    if project.pending_task:
        parts.append(f"等待事项: {project.pending_task.description}")
        if project.pending_task.expected_time:
            parts.append(f"预期时间: {project.pending_task.expected_time}")
    if project.history_summary:
        parts.append(f"项目摘要: {project.history_summary}")
    return "\n".join(parts)


def _detect_pending_task(response: str) -> PendingTask | None:
    """Detect if the response asks the user to perform an action."""
    indicators = [
        "需要你", "请你", "你来", "你自己", "手动", "等待你",
        "等你", "你来做", "需要你完成", "需要实验",
    ]
    for ind in indicators:
        if ind in response:
            desc = response[:200]
            return PendingTask(description=desc, expected_time="")
    return None


def parse_action(raw: str) -> Action:
    try:
        raw = raw.strip()
        if raw.startswith("{"):
            data = json.loads(raw)
        else:
            return Action(action="generate", reasoning="non-JSON response")
        return Action(
            action=data.get("action", "generate"),
            query=data.get("query", ""),
            target=data.get("target", "papers"),
            reasoning=data.get("reasoning", ""),
        )
    except (json.JSONDecodeError, KeyError):
        return Action(action="generate", reasoning="parse error")


def run_agent(user_input: str, llm: LLMProvider, state: AgentState) -> AgentState:
    state.user_input = user_input
    init_db()

    if not state.active_project:
        projects = get_all_projects()
        if projects:
            matched = route_to_project(user_input, projects)
            if matched:
                state.active_project = matched
        if not state.active_project:
            topic = extract_project_topic(user_input)
            if len(topic) > 20:
                try:
                    resp = llm.complete(
                        [{"role": "user", "content": f"Extract a concise project topic (max 5 words) from: {topic}\nOutput ONLY the topic name."}],
                        max_tokens=30,
                    )
                    topic = resp.strip()
                except Exception:
                    topic = topic[:40]
            state.active_project = Project(
                topic=topic,
                status=ProjectStatus.ACTIVE,
                created_at=datetime.now().isoformat(),
                updated_at=datetime.now().isoformat(),
            )
            pid = insert_project(state.active_project)
            state.active_project.id = pid

    if state.active_project and state.active_project.status == ProjectStatus.WAITING:
        resume_msg = _build_resume_message(state.active_project)
        state.user_input = resume_msg + "\n用户: " + user_input
        state.active_project.status = ProjectStatus.ACTIVE
        update_project(state.active_project)

    project_id = state.active_project.id if state.active_project else "default"
    state.conversation_turns = get_recent_turns(project_id, limit=10)

    skills = load_skills()
    skill = find_skill(user_input, skills)
    if skill and skill.handler:
        state = skill.handler(state)
        _save_turn(state, project_id)
        return state

    consecutive_empty = 0
    for round_num in range(1, MAX_ROUNDS + 1):
        state.round_count = round_num

        messages = build_context(state)

        raw = llm.complete(messages, max_tokens=300)
        action = parse_action(raw)

        if blocked := guardrail(action):
            state.final_response = blocked
            _save_turn(state, project_id)
            return state

        if action.action == "retrieve":
            if not action.query.strip():
                state.errors = getattr(state, 'errors', []) + ["empty_query"]
                continue

            build_bm25_index()
            results = hybrid_search(action.query, n_results=5)
            if not results:
                consecutive_empty += 1
            else:
                consecutive_empty = 0
                state.retrieved_context = results
                state.retrieved_chunks = results

            if consecutive_empty >= 3:
                action = Action(action="generate")
            else:
                continue

        if action.action == "generate":
            state.final_response = llm.complete(
                build_context(state) + [{"role": "system", "content": "请基于以上检索结果生成回答。用中文回答。如果有引用来源，请标注。"}],
                max_tokens=2000
            )
            state = validate_response(state)
            _save_turn(state, project_id)
            _maybe_compress(project_id, llm)
            _mark_waiting_if_needed(state)
            return state

        if action.action == "stop":
            state.final_response = "好的。"
            _save_turn(state, project_id)
            return state

    state.final_response = llm.complete(
        build_context(state) + [{"role": "system", "content": "请直接回答用户问题。"}],
        max_tokens=2000
    )
    state = validate_response(state)
    _save_turn(state, project_id)
    _mark_waiting_if_needed(state)
    return state


def _save_turn(state: AgentState, project_id: str):
    round_num = len(state.conversation_turns) + 1 if hasattr(state, 'conversation_turns') else 1
    store_turn(project_id, round_num, state.user_input, state.final_response or "")


def _maybe_compress(project_id: str, llm: LLMProvider):
    uncompressed = count_uncompressed_turns(project_id)
    if uncompressed > 10:
        all_turns = get_recent_turns(project_id, limit=uncompressed)
        old_turns = all_turns[:-5]
        if old_turns:
            turns_text = "\n".join([f"用户: {t.user_message}\n助手: {t.assistant_message}" for t in old_turns])
            summary = llm.complete(
                [{"role": "user", "content": f"将以下对话压缩为简短摘要，保留关键决策、数据、结论:\n{turns_text}"}],
                max_tokens=200
            )
            mark_compressed([t.id for t in old_turns if t.id], summary)


def _mark_waiting_if_needed(state: AgentState):
    if state.final_response and state.active_project:
        task = _detect_pending_task(state.final_response)
        if task:
            state.active_project.status = ProjectStatus.WAITING
            state.active_project.pending_task = task
            update_project(state.active_project)


def process_user_input(state: AgentState, thread_id: str = "default") -> AgentState:
    from research_agent.llm import LiteLLMProvider
    llm = LiteLLMProvider()
    return run_agent(state.user_input, llm, state)


def chat(message: str, state: AgentState | None = None, thread_id: str = "default") -> AgentState:
    if state is None:
        state = AgentState(user_input=message)
    else:
        state.user_input = message
        state.retry_count = 0
        state.retrieved_chunks = []
        state.retrieved_context = []
        state.final_response = ""
        state.error = ""
    return process_user_input(state, thread_id=thread_id)