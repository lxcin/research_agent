"""Self-implemented agent loop with function calling + tool registry."""
import json
import os
import re
import subprocess
import sys
import threading
import uuid as _uuid
from datetime import datetime
from typing import Callable

from research_agent.models import AgentState, Project, ProjectStatus, ConversationTurn, PendingTask, Action
from research_agent.llm import LLMProvider
from research_agent.context import build_context
from research_agent.retrieval import is_vector_available
from research_agent.memory import store_turn, get_recent_turns, count_uncompressed_turns, mark_compressed
from research_agent.store import init_db
from research_agent.router import extract_project_topic
from research_agent.validate import validate_response
from research_agent.config import get_temperature, get_max_output_tokens
from research_agent.trace_log import set_trace_id, logger

MAX_ROUNDS = int(os.environ.get("RESEARCH_AGENT_MAX_ROUNDS", "50"))
MAX_TOTAL_RETRIES = 5
MAX_SEARCH_CALLS = int(os.environ.get("RESEARCH_AGENT_MAX_SEARCH", "10"))
LLM_RETRY_BACKOFF = [1, 2, 4]  # seconds between retries

EventCallback = Callable[[str, dict], None]


def _call_llm_with_retry(llm_func, emit, max_retries=3) -> dict | str:
    """Call LLM with exponential backoff retry. Raises on final failure."""
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return llm_func()
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                delay = LLM_RETRY_BACKOFF[min(attempt, len(LLM_RETRY_BACKOFF) - 1)]
                _emit(emit, "thinking", {"text": f"LLM 重试 {attempt+1}/{max_retries}: {str(e)[:80]}"})
                import time
                time.sleep(delay)
    raise last_error


def _emit(event: EventCallback | None, event_type: str, data: dict):
    if event:
        event(event_type, data)


# Used in tests
def _build_resume_message(project: Project) -> str:
    parts = [f"欢迎回来！项目「{project.topic}」之前处于等待状态。"]
    if project.pending_task:
        parts.append(f"等待事项: {project.pending_task.description}")
        if project.pending_task.expected_time:
            parts.append(f"预期时间: {project.pending_task.expected_time}")
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



def _generate_msgs(messages: list[dict], state) -> list[dict]:
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    results = []
    for i, tm in enumerate(tool_msgs):
        content = tm.get("content", "")[:6000]  # Trim each result
        results.append({"role": "system", "content": f"[工具结果 {i+1}]\n{content}"})
    return [
        {"role": "system", "content": "=== Tool Results / 工具调用结果 (including full paper text / 含论文全文) ==="},
        *results,
        {"role": "system", "content": "=== End of tool results. Answer the user / 工具结果结束，回答用户问题 ==="},
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
    """Stream LLM response token-by-token with retry."""
    return _call_llm_with_retry(
        lambda: _stream_response_once(llm, messages, emit),
        emit,
    )


def _stream_response_once(llm: LLMProvider, messages: list[dict], emit: EventCallback):
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
            _emit(emit, "reply", {"text": delta.content})
    return content


def _auto_validate(state, tc_name, tc_params, messages, on_event):
    """After file_write/file_edit, auto-validate and inject feedback."""
    if tc_name not in ("file_write", "file_edit"):
        return

    path = tc_params.get("path", "")
    if not path:
        return

    from research_agent.tools.builtin.filesystem import _get_project_dir, _safe_path
    proj_dir = _get_project_dir(state)
    full_path = _safe_path(proj_dir, path)
    if not full_path or not os.path.isfile(full_path):
        return

    ext_checks = []

    if path.endswith(".py"):
        ext_checks.append(("syntax", [
            sys.executable, "-c",
            f"import py_compile; py_compile.compile({repr(full_path)}, doraise=True)",
        ]))
        if path.endswith("_test.py") or os.path.basename(path).startswith("test_"):
            ext_checks.append(("tests", [
                sys.executable, "-m", "pytest", full_path, "--tb=short", "-q",
            ]))
    elif path.endswith(".java"):
        ext_checks.append(("compile", ["javac", full_path]))
    else:
        return

    has_error = False
    error_stderr = ""

    for check_name, cmd in ext_checks:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                has_error = True
                error_stderr += f"\n[{check_name} check failed]\n{result.stderr}"
                if result.stdout:
                    error_stderr += f"\n{result.stdout}"
        except FileNotFoundError:
            pass
        except subprocess.TimeoutExpired:
            has_error = True
            error_stderr += f"\n[{check_name} check timed out]"
        except Exception as e:
            has_error = True
            error_stderr += f"\n[{check_name} check error: {e}]"

    if has_error:
        msg = f"[自动验证] {path} 检查失败:\n{error_stderr.strip()}"
        messages.append({"role": "system", "content": msg})
        _emit(on_event, "thinking", {"text": f"自动验证失败: {path} - {error_stderr.strip()[:100]}"})


def run_agent(user_input: str, llm: LLMProvider, state: AgentState,
              on_event: EventCallback = None,
              workspace_dir: str = "", chat_id: str = "") -> AgentState:
    set_trace_id()
    logger.info(f"run_agent START: {user_input[:80]}")
    from research_agent.tools import get_registry
    from research_agent.tools.builtin import register_builtins
    register_builtins()

    state.workspace_dir = workspace_dir
    state.active_chat_id = chat_id
    state.sections = []  # track structured sections for persistence

    # Auto-load MCP servers from config
    import os as _os
    try:
        from research_agent.tools.mcp_loader import MCPManager
        mcp_config = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))),
            "skills", "mcp.yml",
        )
        if _os.path.exists(mcp_config):
            manager = MCPManager(mcp_config)
            import atexit
            atexit.register(manager.shutdown)
            results = manager.start_all()
            for key, names in results.items():
                if names:
                    _emit(on_event, "thinking", {"text": f"MCP: {len(names)} tools from {key}"})
                elif key in results:
                    _emit(on_event, "thinking", {"text": f"MCP: {key} failed"})
    except Exception:
        pass

    registry = get_registry()

    state.user_input = user_input
    init_db()

    # ── Project init / load ──
    from research_agent import project_manager as pm
    if workspace_dir:
        if not pm.is_project_dir(workspace_dir):
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
            proj = pm.init_project(workspace_dir, topic)
            os.makedirs(os.path.join(workspace_dir, "papers"), exist_ok=True)
            os.makedirs(os.path.join(workspace_dir, "experiments"), exist_ok=True)
            try:
                from research_agent.tools.git_tool import git_init
                git_init(workspace_dir)
            except Exception:
                pass
        else:
            proj = pm.load_project(workspace_dir)
    else:
        # No workspace selected — use a default temp workspace so file tools work
        from research_agent.config import get_data_dir
        workspace_dir = str(get_data_dir() / "workspaces" / "default")
        if not pm.is_project_dir(workspace_dir):
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
            proj = pm.init_project(workspace_dir, topic)
        else:
            proj = pm.load_project(workspace_dir)
        os.makedirs(os.path.join(workspace_dir, "papers"), exist_ok=True)
        os.makedirs(os.path.join(workspace_dir, "experiments"), exist_ok=True)

    state.active_project = Project(
        id=pm.get_project_id(workspace_dir) if workspace_dir else "",
        topic=(proj or {}).get("topic", "默认项目"),
        status=ProjectStatus.ACTIVE,
        workspace_dir=workspace_dir,
        created_at=(proj or {}).get("created_at", datetime.now().isoformat()),
        updated_at=datetime.now().isoformat(),
    )

    state.conversation_turns = get_recent_turns(workspace_dir, chat_id, limit=20)

    tools_list = registry.list_for_llm()

    # ── Agent loop with function calling ──
    consecutive_empty = 0
    total_search_rounds = 0
    total_retries = 0
    consecutive_retrieve_few = 0
    search_papers_found = False  # trigger dynamic tool filtering

    model_name = getattr(llm, "model", "")
    messages = build_context(state, registry, model_name)
    messages.insert(1, {"role": "system", "content": registry.generate_capabilities()})

    for round_num in range(1, MAX_ROUNDS + 1):
        state.round_count = round_num
        # Dynamic filtering: after search_papers finds results, remove retrieve
        # to force read_paper — LLM cannot access arXiv results via retrieve
        if search_papers_found:
            tools_list = [t for t in tools_list if t["function"]["name"] != "retrieve"]

        if total_search_rounds >= 3 and consecutive_empty >= 2:
            _emit(on_event, "thinking", {"text": "已尝试多次搜索无果，直接回答..."})
            state.final_response = _stream_response(llm, _generate_msgs(messages, state), on_event)
            break
        if total_search_rounds >= MAX_SEARCH_CALLS:
            _emit(on_event, "thinking", {"text": f"检索次数已达上限({MAX_SEARCH_CALLS})，选最好的论文用 read_paper 读，或直接基于当前结果回答"})
            messages.append({"role": "system",
                "content": f"检索次数已达上限({MAX_SEARCH_CALLS}次)。请从已有结果中选出最相关的论文用 read_paper 阅读，或直接基于当前检索结果回答。不要再调用 search_papers。"})
            # Don't reset counter — keep blocking subsequent calls
        if total_retries >= MAX_TOTAL_RETRIES:
            _emit(on_event, "thinking", {"text": "重试次数已达上限"})
            state.final_response = _stream_response(llm, _generate_msgs(messages, state), on_event)
            break

        try:
            response = _call_llm_with_retry(
                lambda: _call_llm_with_tools(llm, messages, tools_list, "auto"),
                on_event,
            )
        except Exception as e:
            total_retries += 1
            _emit(on_event, "thinking", {"text": f"模型调用失败 (尝试 {total_retries}/{MAX_TOTAL_RETRIES}): {str(e)[:100]}"})
            messages.append({"role": "system", "content": f"模型调用失败: {e}。请调整参数重试。"})
            if total_retries >= MAX_TOTAL_RETRIES:
                state.final_response = f"抱歉，模型多次调用失败。"
                _save_turn(state, project_id)
                return state
            continue

        # ── Process tool calls ──
        tool_calls = response.get("tool_calls", [])
        round_action_names = [tc["name"] for tc in tool_calls] if tool_calls else []
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
            round_has_errors = False
            for tc in tool_calls:
                tc_id = tc["id"]
                tc_name = tc["name"]
                tc_input = tc["params"]

                _emit(on_event, "tool_start", {"id": tc_id, "name": tc_name, "input": tc_input})
                state._current_tool_id = tc_id

                # ── Guardrail: HITL confirmation for dangerous commands ──
                if tc_name == "shell_exec":
                    from research_agent.guardrail import guardrail as g_rail
                    block_reason = g_rail(Action(action=tc_name, query=tc_input.get("command", "")))
                    if block_reason:
                        confirm_id = str(_uuid.uuid4())[:8]
                        command_text = tc_input.get("command", "")
                        _emit(on_event, "confirm_required", {
                            "id": confirm_id,
                            "tool": tc_name,
                            "command": command_text[:200],
                            "reason": block_reason
                        })
                        confirm_event = threading.Event()
                        state._pending_confirms[confirm_id] = {"event": confirm_event, "approved": False}
                        confirm_event.wait(timeout=60)
                        confirmed = state._pending_confirms.pop(confirm_id, {}).get("approved", False)
                        if not confirmed:
                            cancel_msg = f"User cancelled: {block_reason}"
                            _emit(on_event, "tool_end", {"id": tc_id, "name": tc_name, "status": "error", "output": {"error": cancel_msg}})
                            messages.append({"role": "tool", "tool_call_id": tc["id"],
                                             "content": json.dumps({"error": cancel_msg}, ensure_ascii=False)})
                            continue

                if tc["name"] in ("retrieve", "search_papers"):
                    total_search_rounds += 1

                # Block search_papers after limit
                if tc["name"] == "search_papers" and total_search_rounds >= MAX_SEARCH_CALLS:
                    hint = f"搜索次数已达上限({MAX_SEARCH_CALLS})。请用 read_paper 或直接基于已有结果回答。"
                    _emit(on_event, "tool_end", {"id": tc_id, "name": tc_name, "status": "error", "output": {"error": hint}})
                    messages.append({"role": "tool", "tool_call_id": tc["id"],
                                     "content": json.dumps({"error": hint}, ensure_ascii=False)})
                    continue

                # Validate tool params before dispatch
                from research_agent.tools.validate_params import validate_tool_params
                param_err = validate_tool_params(tc["name"], tc["params"])
                if param_err:
                    total_retries += 1; round_retry += 1; round_has_errors = True
                    _emit(on_event, "tool_end", {"id": tc_id, "name": tc_name, "status": "error", "output": {"error": param_err}})
                    messages.append({"role": "tool", "tool_call_id": tc["id"],
                                     "content": json.dumps({"error": param_err}, ensure_ascii=False)})
                    continue

                result = registry.dispatch(tc["name"], tc["params"], llm, state, on_event)

                if not result.success and result.data.get("error"):
                    total_retries += 1; round_retry += 1; round_has_errors = True
                    err_detail = result.data.get("error", "")
                    if "stderr" in result.data and result.data["stderr"]:
                        err_detail += f"\nstderr: {result.data['stderr'][:500]}"
                    if "stdout" in result.data and result.data["stdout"]:
                        err_detail += f"\nstdout: {result.data['stdout'][:300]}"
                    hint = f"工具'{tc['name']}'失败: {err_detail}"
                    if round_retry >= 2:
                        hint += " 请换其他方式回答。"
                    _emit(on_event, "tool_end", {"id": tc_id, "name": tc_name, "status": "error", "output": {"error": result.data["error"]}})
                    messages.append({"role": "tool", "tool_call_id": tc["id"],
                                     "content": json.dumps({"error": hint}, ensure_ascii=False)})
                    continue

                # Also catch shell_exec returning success=False in data
                if tc["name"] == "shell_exec" and result.data.get("success") is False:
                    stderr = result.data.get("stderr", "")
                    stdout = result.data.get("stdout", "")[:300]
                    err = stderr or result.data.get("returncode", "")
                    if stderr:
                        hint = f"Command failed (exit {result.data.get('returncode', '?')}): {stderr.strip()[:200]}"
                    else:
                        hint = f"Command failed with exit code {result.data.get('returncode', '?')}"
                    _emit(on_event, "tool_end", {"id": tc_id, "name": tc_name, "status": "error", "output": {"error": hint[:100]}})
                    messages.append({"role": "tool", "tool_call_id": tc["id"],
                                     "content": json.dumps({"error": hint, "stdout": stdout}, ensure_ascii=False)})
                    continue

                # Success
                _emit(on_event, "tool_end", {"id": tc_id, "name": tc_name, "status": "success", "output": result.data})
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": json.dumps(result.data, ensure_ascii=False)})

                if tc_name in ("file_write", "file_edit"):
                    _auto_validate(state, tc_name, tc["params"], messages, on_event)

                if tc["name"] == "search_papers" and result.data.get("found", 0) > 0:
                    search_papers_found = True

                if result.chunks:
                    state.retrieved_context = _deduplicate_results(result.chunks)
                    state.retrieved_chunks = state.retrieved_context
                    consecutive_empty = 0
                    if tc["name"] == "retrieve":
                        if len(result.chunks) < 3:
                            consecutive_retrieve_few += 1
                        else:
                            consecutive_retrieve_few = 0
                        # P/R instrumentation was tied to vector RAG (retired in V4).
                        if llm and is_vector_available():
                            try:
                                _evaluate_retrieval(llm, tc["params"].get("query", ""), result.chunks, on_event)
                            except Exception:
                                pass
                elif tc["name"] in ("retrieve", "search_papers") and not result.success:
                    consecutive_empty += 1
                    _emit(on_event, "thinking", {"text": f"{tc['name']} 返回空结果 (连续 {consecutive_empty} 次)"})
                    if tc["name"] == "retrieve":
                        consecutive_retrieve_few += 1

            if consecutive_empty >= 3:
                _emit(on_event, "thinking", {"text": "连续搜索无果，直接回答..."})
                state.final_response = _stream_response(llm, _generate_msgs(messages, state), on_event)
                break
            if consecutive_retrieve_few >= 2:
                _emit(on_event, "thinking", {"text": "本地结果不足，建议使用 search_papers"})
                messages.append({"role": "system", "content": "本地检索结果较少（<3条），建议使用 search_papers 搜索 arXiv 获取更多论文。"})
                consecutive_retrieve_few = 0
            # Auto-checkpoint after successful rounds with file/shell operations
            try:
                from research_agent.tools.git_tool import git_checkpoint, should_auto_checkpoint
                if state.active_project:
                    from research_agent.tools.builtin.filesystem import _get_project_dir
                    ws = _get_project_dir(state)
                    if ws and os.path.isdir(os.path.join(ws, ".git")) and should_auto_checkpoint(round_action_names, round_has_errors):
                        git_checkpoint(ws, f"round_{round_num}: auto checkpoint")
            except Exception:
                pass
            continue

        # ── No tool calls → text response ──
        # Keep tool results so LLM can reference what was done
        clean_msgs = [m for m in messages if m["role"] in ("system", "user", "tool", "assistant")]
        clean_msgs.append({"role": "system",
            "content": "基于以上工具调用结果和对话历史，用简洁的方式总结你完成了什么、结果如何。引用具体数据但不要重复完整内容。使用与用户相同的语言。"})
        clean_msgs.append({"role": "user", "content": state.user_input})
        state.final_response = _stream_response(llm, clean_msgs, on_event)
        break

    # ── If no response generated yet (shouldn't happen with function calling) ──
    if not state.final_response:
        state.final_response = _stream_response(llm, _generate_msgs(messages, state), on_event)

    # ── Stream final response ──
    state = validate_response(state)
    _save_turn(state, workspace_dir, chat_id)
    _maybe_compress(workspace_dir, chat_id, llm)
    _maybe_distill(state, workspace_dir, chat_id, llm)
    _mark_waiting_if_needed(state)
    return state


def _save_turn(state: AgentState, workspace_dir: str, chat_id: str):
    round_num = len(state.conversation_turns) + 1 if hasattr(state, 'conversation_turns') else 1
    store_turn(workspace_dir, chat_id, round_num, state.user_input, state.final_response or "",
               sections=getattr(state, 'sections', None))


def _maybe_distill(state: AgentState, workspace_dir: str, chat_id: str, llm: LLMProvider):
    """Submit this turn's conversation-only content to the Tier B pipeline.

    Runs after the turn is persisted; non-blocking background distillation.
    Skipped when memory.enabled=false or the response carried tool traces.
    """
    if not workspace_dir or not chat_id:
        return
    try:
        from research_agent.memory import source as mem_source
        from research_agent.memory import pipeline as mem_pipeline
        from research_agent.config import get_memory_config
        if not get_memory_config().get("enabled", True):
            return
        snap = mem_source.build_extraction_source(workspace_dir, chat_id)
        if not snap["has_content"]:
            return
        src = {}
        if state.active_project and getattr(state.active_project, "id", None):
            src["project_id"] = state.active_project.id
        src["chat_id"] = chat_id
        mem_pipeline.submit(snap["conversation"], llm, notes=snap["notes"],
                            source=src)
    except Exception:
        pass


def _maybe_compress(workspace_dir: str, chat_id: str, llm: LLMProvider):
    uncompressed = count_uncompressed_turns(workspace_dir, chat_id)
    if uncompressed > 10:
        all_turns = get_recent_turns(workspace_dir, chat_id, limit=uncompressed)
        old_turns = all_turns[:-5]
        if old_turns:
            turns_text = "\n".join([f"用户: {t.user_message}\n助手: {t.assistant_message}" for t in old_turns])
            summary = llm.complete(
                [{"role": "user", "content": 
                    f"将以下对话压缩为摘要，分两个字段输出JSON：\n"
                    f"1. conclusions: 关键决策、数据、已确认结论（1-2句）\n"
                    f"2. dead_ends: 尝试过但不可行的方向、被推翻的假设、已验证不可行的方法（保留这些很重要，避免重复犯错）\n"
                    f"输出JSON: {{\"conclusions\": \"...\", \"dead_ends\": \"...\"}}\n"
                    f"对话:\n{turns_text}"}],
                max_tokens=200
            )
            indices = [i for i, t in enumerate(old_turns) if t.id]
            mark_compressed(workspace_dir, chat_id, indices, summary)

            try:
                from research_agent import project_manager as pm
                existing_progress = pm.load_progress(workspace_dir)
                progress_prompt = f"基于以下对话，用一句话总结当前项目进度（已完成什么、正在做什么、下一步做什么）:\n{turns_text}"
                progress = llm.complete([{"role": "user", "content": progress_prompt}], max_tokens=100)
                new_progress = existing_progress + f"\n[进度] {progress}" if existing_progress else f"[进度] {progress}"
                pm.update_progress(workspace_dir, new_progress)
            except Exception:
                pass


def _mark_waiting_if_needed(state: AgentState):
    if state.final_response and state.active_project:
        task = _detect_pending_task(state.final_response)
        if task:
            state.active_project.status = ProjectStatus.WAITING
            state.active_project.pending_task = task


def process_user_input(state: AgentState, thread_id: str = "default") -> AgentState:
    from research_agent.llm import LiteLLMProvider
    llm = LiteLLMProvider()
    return run_agent(state.user_input, llm, state,
                     workspace_dir=getattr(state, 'workspace_dir', ''),
                     chat_id=getattr(state, 'active_chat_id', ''))


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