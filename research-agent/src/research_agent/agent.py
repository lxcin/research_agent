"""LangGraph agent: router -> reasoner -> retriever -> generator with loop."""
import json
from datetime import datetime

import litellm
from langgraph.graph import StateGraph, END

from research_agent.models import AgentState, Project, ProjectStatus, PlanStep, PendingTask, AccumulatedWisdom
from research_agent.store import init_db, get_all_projects, insert_project
from research_agent.router import route_to_project, should_create_project, extract_project_topic
from research_agent.retrieval import hybrid_search, build_bm25_index
from research_agent.loop import self_check, evaluate_retrieval_sufficiency
from research_agent.compressor import should_compress, compress


def router_node(state: AgentState) -> AgentState:
    init_db()
    projects = get_all_projects()

    if not projects:
        state.active_project = Project(
            topic=extract_project_topic(state.user_input),
            status=ProjectStatus.ACTIVE,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        pid = insert_project(state.active_project)
        state.active_project.id = pid
        return state

    matched = route_to_project(state.user_input, projects)
    if matched:
        state.active_project = matched
    elif should_create_project(state.user_input, projects):
        response = litellm.completion(
            model="claude-3-haiku-20240307",
            messages=[{"role": "user", "content": f"Extract a concise project topic (max 10 words) from this message, output ONLY the topic: {state.user_input}"}],
            max_tokens=30,
        )
        topic = response.choices[0].message.content.strip()
        state.active_project = Project(
            topic=topic,
            status=ProjectStatus.ACTIVE,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        pid = insert_project(state.active_project)
        state.active_project.id = pid
    else:
        state.active_project = projects[0] if projects else None

    from research_agent.skill import load_skills, find_skill
    skills = load_skills()
    skill = find_skill(state.user_input, skills)
    if skill and skill.handler:
        state = skill.handler(state)
        return state

    return state


def reasoner_node(state: AgentState) -> AgentState:
    if state.retry_count >= 3:
        return state

    project_info = f"Project: {state.active_project.topic}. Summary: {state.active_project.history_summary}" if state.active_project else "No active project"
    prompt = f"""You are a research assistant.
{project_info}
User message: {state.user_input}

Determine if you need to search the knowledge base. Output JSON:
{{"needs_retrieval": true/false, "search_query": "concise search terms", "reasoning": "why"}}"""

    response = litellm.completion(
        model="claude-3-haiku-20240307",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=150,
    )
    content = response.choices[0].message.content.strip()
    try:
        decision = json.loads(content)
    except json.JSONDecodeError:
        decision = {"needs_retrieval": True, "search_query": state.user_input}

    state.search_query = decision.get("search_query", state.user_input)
    state.needs_retrieval = decision.get("needs_retrieval", True)
    return state


def retriever_node(state: AgentState) -> AgentState:
    if not getattr(state, "needs_retrieval", True):
        return state

    query = getattr(state, "search_query", state.user_input)
    build_bm25_index()
    results = hybrid_search(query, n_results=10)
    state.retrieved_chunks = results
    state.retrieval_sufficient = evaluate_retrieval_sufficiency(state)
    return state


def generator_node(state: AgentState) -> AgentState:
    chunks_text = "\n".join([
        f"[{i+1}] Source: paper {c.get('paper_id', 'unknown')}, score={c.get('source_score', 'N/A')}\n"
        f"Content: {c['text'][:500]}"
        for i, c in enumerate(state.retrieved_chunks[:5])
    ]) if state.retrieved_chunks else "No relevant documents found in knowledge base."

    confidence_map = {"certain": "确定", "speculative": "推测", "uncertain": "不确定"}

    prompt = f"""You are a research partner agent. Below is context from the knowledge base and the user's message.
Generate a thorough, cited response. Mark your confidence level for each major claim.

Current project: {state.active_project.topic if state.active_project else 'None'}
Project history: {state.active_project.history_summary if state.active_project and state.active_project.history_summary else 'New project'}

Knowledge base context:
{chunks_text}

User message: {state.user_input}

Respond in Chinese. After your response, append:
---
引用来源: [list sources by number]
自信度: [确定/推测/不确定] - [brief reason]
"""

    response = litellm.completion(
        model="claude-3-haiku-20240307",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
    )
    state.final_response = response.choices[0].message.content

    state = self_check(state)

    if state.retrieved_chunks:
        state.citations = [f"paper:{c.get('paper_id', '?')}" for c in state.retrieved_chunks[:5]]

    if "不确定" in state.final_response:
        state.confidence = "uncertain"
    elif "推测" in state.final_response:
        state.confidence = "speculative"
    else:
        state.confidence = "certain"

    return state


def after_response(state: AgentState) -> str:
    if not state.retrieval_sufficient and state.retry_count < 3:
        return "reasoner"
    if state.error:
        state.retry_count += 1
        return "reasoner" if state.retry_count < 3 else "__end__"

    if should_compress(state):
        compress(state)
        state.needs_compression = False
    return "__end__"


def should_retrieve(state: AgentState) -> str:
    if getattr(state, "needs_retrieval", True):
        return "retriever"
    return "generator"


def build_graph() -> StateGraph:
    workflow = StateGraph(AgentState)

    workflow.add_node("router", router_node)
    workflow.add_node("reasoner", reasoner_node)
    workflow.add_node("retriever", retriever_node)
    workflow.add_node("generator", generator_node)

    workflow.set_entry_point("router")
    workflow.add_edge("router", "reasoner")
    workflow.add_conditional_edges("reasoner", should_retrieve, {
        "retriever": "retriever",
        "generator": "generator",
    })
    workflow.add_edge("retriever", "generator")
    workflow.add_conditional_edges("generator", after_response, {
        "reasoner": "reasoner",
        "__end__": END,
    })

    return workflow


_graph = None


def get_graph() -> StateGraph:
    global _graph
    if _graph is None:
        import sqlite3
        from langgraph.checkpoint.sqlite import SqliteSaver
        from research_agent.config import get_data_dir
        db_path = str(get_data_dir() / "checkpoint.db")
        conn = sqlite3.connect(db_path, check_same_thread=False)
        checkpointer = SqliteSaver(conn)
        _graph = build_graph().compile(checkpointer=checkpointer)
    return _graph


def process_user_input(state: AgentState, thread_id: str = "default") -> AgentState:
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke(state, config)
    return AgentState(**result) if isinstance(result, dict) else result


def chat(message: str, state: AgentState | None = None, thread_id: str = "default") -> AgentState:
    if state is None:
        state = AgentState(user_input=message)
    else:
        state.user_input = message
        state.retry_count = 0
        state.retrieved_chunks = []
        state.final_response = ""
        state.error = ""
    return process_user_input(state, thread_id=thread_id)