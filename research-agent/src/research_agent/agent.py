"""Self-implemented agent loop with function calling + tool registry."""
import json
import re
import threading
from datetime import datetime
from typing import Callable

from research_agent.models import AgentState, Action, Project, ProjectStatus, ConversationTurn, PendingTask
from research_agent.llm import LLMProvider
from research_agent.context import build_context
from research_agent.guardrail import guardrail
from research_agent.retrieval import hybrid_search, build_bm25_index
from research_agent.memory import store_turn, get_recent_turns, count_uncompressed_turns, mark_compressed
from research_agent.store import init_db, get_all_projects, insert_project, update_project
from research_agent.router import route_to_project, extract_project_topic
from research_agent.validate import validate_response
from research_agent.config import get_temperature, get_max_output_tokens

MAX_ROUNDS = 5
MAX_TOTAL_RETRIES = 5

EventCallback = Callable[[str, dict], None]


def _emit(event: EventCallback | None, event_type: str, data: dict):
    if event:
        event(event_type, data)


def _build_resume_message(project: Project) -> str:
    parts = [f"欢迎回来！项目「{project.topic}」之前处于等待状态。"]
    if project.pending_task:
        parts.append(f"等待事项: {project.pending_task.description}")
        if project.pending_task.expected_time:
            parts.append(f"预期时间: {project.pending_task.expected_time}")
    if project.history_summary:
        parts.append(f"项目摘要: {project.history_summary}")
    return "\n".join(parts)


def _detect_pending_task(response: str) -> PendingTask | None:
    indicators = [
        "需要你", "请你", "你来", "你自己", "手动", "等待你",
        "等你", "你来做", "需要你完成", "需要实验",
    ]
    for ind in indicators:
        if ind in response:
            return PendingTask(description=response[:200], expected_time="")
    return None


def _deduplicate_results(results: list[dict]) -> list[dict]:
    seen = {}
    for r in results:
        pid = r.get("paper_id", r.get("id", ""))
        if not pid:
            continue
        if pid not in seen:
            seen[pid] = r
    return list(seen.values())


def _clean_context(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m["role"] in ("system", "user", "tool")]


def _generate_msgs(messages: list[dict], state) -> list[dict]:
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    results = []
    for i, tm in enumerate(tool_msgs):
        content = tm.get("content", "")[:6000]  # Trim each result
        results.append({"role": "system", "content": f"[工具结果 {i+1}]\n{content}"})
    return [
        {"role": "system", "content": "=== 以下是本轮所有工具调用结果（包括读过的论文全文） ==="},
        *results,
        {"role": "system", "content": "=== 以上是工具结果。现在基于这些结果回答用户问题，引用用 [N] 标注。 ==="},
        {"role": "user", "content": state.user_input},
    ]


def _parse_json_flex(raw: str):
    text = re.sub(r'^```(?:json)?\s*\n?', '', raw.strip())
    text = re.sub(r'\n?```\s*$', '', text)
    return json.loads(text.strip())


def _evaluate_retrieval(llm, query: str, chunks: list[dict], on_event: EventCallback):
    """Evaluate: Precision@5/@8/@10 + Recall via broad-search pool."""
    if not chunks:
        return
    k_max = min(len(chunks), 10)
    if k_max < 1:
        return

    # Step 1: evaluate all retrieved chunks (up to 10)
    items_k = "\n".join([f"[{i+1}] {c.get('text', '')[:150]}" for i, c in enumerate(chunks[:k_max])])
    prompt_k = f"""对于查询"{query}"，判断每个片段是否相关。["relevant"/"irrelevant"] JSON数组：

{items_k}

JSON："""
    try:
        raw = llm.complete([{"role": "user", "content": prompt_k}], max_tokens=300)
        labels_k = _parse_json_flex(raw)
    except Exception:
        return
    if not isinstance(labels_k, list):
        return

    # Compute precision at different k
    def prec_at(n: int) -> float:
        labels = labels_k[:n]
        if not labels:
            return 0.0
        return sum(1 for r in labels if str(r).lower().strip() == "relevant") / len(labels)

    p5 = prec_at(min(5, k_max))
    p8 = prec_at(min(8, k_max))
    p10 = prec_at(min(10, k_max))

    top_ids = set()
    for i, label in enumerate(labels_k):
        if str(label).lower().strip() == "relevant" and i < len(chunks):
            pid = chunks[i].get("paper_id", chunks[i].get("id", ""))
            if pid:
                top_ids.add(pid)

    # Step 2: Broad search for recall pool
    from research_agent.retrieval import hybrid_search
    broader = hybrid_search(query, n_results=50)
    if not broader:
        _emit(on_event, "recall", {"query": query[:60], "p5": f"{p5:.0%}", "p8": f"{p8:.0%}",
                "p10": f"{p10:.0%}", "recall": "N/A", "reason": "DB empty"})
        return

    # Eval broad pool (limited to 25 to save tokens)
    pool_size = min(len(broader), 25)
    items_broad = "\n".join([f"[{i+1}] {c.get('text', '')[:120]}" for i, c in enumerate(broader[:pool_size])])
    prompt_broad = f"""对于查询"{query}"，判断每个片段是否相关。["relevant"/"irrelevant"] JSON数组：

{items_broad}

JSON："""
    try:
        raw = llm.complete([{"role": "user", "content": prompt_broad}], max_tokens=400)
        labels_broad = _parse_json_flex(raw)
    except Exception:
        labels_broad = []

    pool_ids = set()
    if isinstance(labels_broad, list):
        for i, r in enumerate(labels_broad):
            if str(r).lower().strip() == "relevant" and i < len(broader):
                pid = broader[i].get("paper_id", broader[i].get("id", ""))
                if pid:
                    pool_ids.add(pid)

    pool_sz = len(pool_ids)
    if pool_sz == 0:
        _emit(on_event, "recall", {"query": query[:60], "p5": f"{p5:.0%}", "p8": f"{p8:.0%}",
                "p10": f"{p10:.0%}", "recall": "N/A", "reason": "no relevant in DB", "pool": 0})
        return

    recall_hits = len(top_ids & pool_ids)
    recall_val = recall_hits / pool_sz

    _emit(on_event, "recall", {
        "query": query[:60],
        "p5": f"{p5:.0%}", "p8": f"{p8:.0%}", "p10": f"{p10:.0%}",
        "recall": f"{recall_val:.0%}",
        "recall_hits": recall_hits,
        "recall_pool": pool_sz,
    })



def _call_llm_with_tools(llm: LLMProvider, messages: list[dict],
                         tools: list[dict], tool_choice: str = "auto") -> dict:
    """Call LLM with function calling support. Returns {content, tool_calls}."""
    import litellm
    model = getattr(llm, "model", "openai/deepseek-chat")
    kwargs = getattr(llm, "_kwargs", {})
    api_key = getattr(llm, "api_key", None)

    resp = litellm.completion(
        model=model, messages=messages,
        tools=tools if tools else None,
        tool_choice=tool_choice if tools else None,
        temperature=get_temperature(0.3),
        api_key=api_key, **kwargs,
    )
    msg = resp.choices[0].message
    result = {"content": msg.content or ""}
    if msg.tool_calls:
        result["tool_calls"] = [
            {"id": tc.id, "name": tc.function.name,
             "params": json.loads(tc.function.arguments)}
            for tc in msg.tool_calls
        ]
    return result


def _stream_response(llm: LLMProvider, messages: list[dict], emit: EventCallback):
    """Stream LLM response token-by-token, emitting chunk events."""
    import litellm
    model = getattr(llm, "model", "openai/deepseek-chat")
    kwargs = getattr(llm, "_kwargs", {})
    api_key = getattr(llm, "api_key", None)
    litellm_kw = dict(model=model, messages=messages,
                      temperature=get_temperature(0.7),
                      api_key=api_key, stream=True, **kwargs)
    mt = get_max_output_tokens()
    if mt: litellm_kw["max_tokens"] = mt
    resp = litellm.completion(**litellm_kw)
    content = ""
    for chunk in resp:
        delta = chunk.choices[0].delta
        if delta.content:
            content += delta.content
            _emit(emit, "chunk", {"text": delta.content})
    return content


def run_agent(user_input: str, llm: LLMProvider, state: AgentState,
              on_event: EventCallback = None) -> AgentState:
    from research_agent.tools import get_registry
    from research_agent.tools.builtin import register_builtins
    from research_agent.skill import load_and_register_skills
    register_builtins()
    load_and_register_skills()
    registry = get_registry()

    state.user_input = user_input
    init_db()

    _emit(on_event, "step", {"step": "init", "text": "分析用户意图..."})

    # Emit a plan for complex tasks
    if len(user_input) > 10 and any(kw in user_input for kw in ["综述", "review", "总结", "比较", "设计", "实现", "复现", "报告"]):
        _emit(on_event, "plan", {"items": [
            "🔍 检索本地知识库",
            "📡 搜索 arXiv 补充论文",
            "📊 分析比较各方观点",
            "✍️ 生成结构化回答",
        ]})

    # ── Project routing ──
    if not state.active_project:
        projects = get_all_projects()
        if projects:
            matched = route_to_project(user_input, projects)
            if matched:
                state.active_project = matched
                _emit(on_event, "step", {"step": "route", "text": f"路由到项目: {matched.topic}"})
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
                topic=topic, status=ProjectStatus.ACTIVE,
                created_at=datetime.now().isoformat(), updated_at=datetime.now().isoformat(),
            )
            pid = insert_project(state.active_project)
            state.active_project.id = pid
            # Create workspace directory for this project
            from research_agent.tools.builtin.filesystem import _get_project_dir
            ws_dir = _get_project_dir(state)
            import os
            os.makedirs(os.path.join(ws_dir, "papers"), exist_ok=True)
            os.makedirs(os.path.join(ws_dir, "experiments"), exist_ok=True)
            _emit(on_event, "step", {"step": "project_created", "text": f"创建项目: {topic}\n工作区: {ws_dir}"})

    if state.active_project and state.active_project.status == ProjectStatus.WAITING:
        resume_msg = _build_resume_message(state.active_project)
        state.user_input = resume_msg + "\n用户: " + user_input
        state.active_project.status = ProjectStatus.ACTIVE
        update_project(state.active_project)

    project_id = state.active_project.id if state.active_project else "default"
    state.conversation_turns = get_recent_turns(project_id, limit=20)

    # ── Agent loop with function calling ──
    tools_list = registry.list_for_llm()
    consecutive_empty = 0
    total_search_rounds = 0
    total_retries = 0
    consecutive_retrieve_few = 0  # retrieve rounds with < 3 results

    model_name = getattr(llm, "model", "")
    messages = build_context(state, registry, model_name)

    for round_num in range(1, MAX_ROUNDS + 1):
        state.round_count = round_num

        if total_search_rounds >= 3 and consecutive_empty >= 2:
            _emit(on_event, "step", {"step": "giving_up", "text": "已尝试多次搜索无果，直接回答..."})
            state.final_response = _stream_response(llm, _generate_msgs(messages, state), on_event)
            break
        if total_retries >= MAX_TOTAL_RETRIES:
            _emit(on_event, "step", {"step": "giving_up", "text": "重试次数已达上限"})
            state.final_response = _stream_response(llm, _generate_msgs(messages, state), on_event)
            break

        _emit(on_event, "step", {"step": "thinking", "round": round_num, "text": "思考下一步..."})

        try:
            response = _call_llm_with_tools(llm, messages, tools_list, "auto")
        except Exception as e:
            total_retries += 1
            _emit(on_event, "tool", {"tool": "llm", "status": "error", "error": str(e), "retries": total_retries})
            messages.append({"role": "system", "content": f"模型调用失败: {e}。请调整参数重试。"})
            if total_retries >= MAX_TOTAL_RETRIES:
                state.final_response = f"抱歉，模型多次调用失败。"
                _save_turn(state, project_id)
                return state
            continue

        # ── Process tool calls ──
        tool_calls = response.get("tool_calls", [])
        if tool_calls:
            messages.append({
                "role": "assistant", "content": None,
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": json.dumps(tc["params"])}}
                    for tc in tool_calls
                ],
            })

            round_retry = 0
            for tc in tool_calls:
                _emit(on_event, "action", {
                    "action": tc["name"], "query": json.dumps(tc["params"], ensure_ascii=False)[:100],
                    "reasoning": "", "round": round_num,
                })

                if tc["name"] in ("retrieve", "search_papers"):
                    total_search_rounds += 1

                if tc["name"] not in registry:
                    total_retries += 1; round_retry += 1
                    hint = f"工具'{tc['name']}'不存在。可用: {', '.join(registry.tools.keys())}"
                    if round_retry >= 2:
                        hint += " 请直接回答，不要调用工具。"
                    _emit(on_event, "tool", {"tool": tc["name"], "status": "unknown", "hint": hint[:80]})
                    messages.append({"role": "tool", "tool_call_id": tc["id"],
                                     "content": json.dumps({"error": hint}, ensure_ascii=False)})
                    continue

                result = registry.dispatch(tc["name"], tc["params"], llm, state, on_event)

                if not result.success and result.data.get("error"):
                    total_retries += 1; round_retry += 1
                    err_detail = result.data.get("error", "")
                    if "stderr" in result.data and result.data["stderr"]:
                        err_detail += f"\nstderr: {result.data['stderr'][:500]}"
                    if "stdout" in result.data and result.data["stdout"]:
                        err_detail += f"\nstdout: {result.data['stdout'][:300]}"
                    hint = f"工具'{tc['name']}'失败: {err_detail}"
                    if round_retry >= 2:
                        hint += " 请换其他方式回答。"
                    _emit(on_event, "tool", {"tool": tc["name"], "status": "error", "error": result.data["error"]})
                    messages.append({"role": "tool", "tool_call_id": tc["id"],
                                     "content": json.dumps({"error": hint}, ensure_ascii=False)})
                    continue

                # Success
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": json.dumps(result.data, ensure_ascii=False)})

                if result.chunks:
                    state.retrieved_context = _deduplicate_results(result.chunks)
                    state.retrieved_chunks = state.retrieved_context
                    consecutive_empty = 0
                    if tc["name"] == "retrieve":
                        if len(result.chunks) < 3:
                            consecutive_retrieve_few += 1
                        else:
                            consecutive_retrieve_few = 0
                        if llm:
                            try:
                                _evaluate_retrieval(llm, tc["params"].get("query", ""), result.chunks, on_event)
                            except Exception:
                                pass
                elif tc["name"] in ("retrieve", "search_papers") and not result.success:
                    consecutive_empty += 1
                    _emit(on_event, "tool", {"tool": tc["name"], "status": "empty", "consecutive": consecutive_empty})
                    if tc["name"] == "retrieve":
                        consecutive_retrieve_few += 1

            if consecutive_empty >= 3:
                _emit(on_event, "step", {"step": "giving_up", "text": "连续搜索无果"})
                state.final_response = _stream_response(llm, _generate_msgs(messages, state), on_event)
                break
            if consecutive_retrieve_few >= 2:
                _emit(on_event, "tool", {"tool": "hint", "status": "escalate",
                       "hint": "本地结果不足，建议使用 search_papers"})
                messages.append({"role": "system", "content": "本地检索结果较少（<3条），建议使用 search_papers 搜索 arXiv 获取更多论文。"})
                consecutive_retrieve_few = 0
            continue

        # ── No tool calls → text response ──
        _emit(on_event, "step", {"step": "generate", "text": "生成回答..."})
        tool_msgs = [m for m in messages if m["role"] in ("tool",)]
        generate_msgs = [
            {"role": "system", "content": "=== 以下是所有工具调用结果（包括读过的论文全文） ==="},
            *tool_msgs,
            {"role": "system", "content": "=== 以上是工具结果。现在基于这些结果回答用户问题。引用时用 [N] 标注。 ==="},
            {"role": "user", "content": state.user_input},
        ]
        state.final_response = _stream_response(llm, generate_msgs, on_event)
        break

    # ── If no response generated yet (shouldn't happen with function calling) ──
    if not state.final_response:
        state.final_response = _stream_response(llm, _generate_msgs(messages, state), on_event)

    # ── Stream final response ──
    state = validate_response(state)
    _save_turn(state, project_id)
    _maybe_compress(project_id, llm)
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

            # Generate progress summary and write to notes
            try:
                from research_agent.store import get_project, update_project
                project = get_project(project_id)
                if project:
                    progress_prompt = f"基于以下对话，用一句话总结当前项目进度（已完成什么、正在做什么、下一步做什么）:\n{turns_text}"
                    progress = llm.complete([{"role": "user", "content": progress_prompt}], max_tokens=100)
                    existing = getattr(project.accumulated_wisdom, 'notes', "") or ""
                    project.accumulated_wisdom.notes = existing + f"\n[进度] {progress}" if existing else f"[进度] {progress}"
                    update_project(project)
            except Exception:
                pass


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