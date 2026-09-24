"""Host layer for the PaperPilot agent: workspace/project setup, tool
registration, diagnostics wiring, and post-run persistence hooks.

The actual agent strategy (loop + function calling) lives in research_agent.
runtime (AgentRuntime / FunctionCallingRuntime) and is replaceable.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Callable

from research_agent.models import AgentState, Project, ProjectStatus, PendingTask
from research_agent.llm import LLMProvider
from research_agent.memory import store_turn, get_recent_turns, count_uncompressed_turns, mark_compressed
from research_agent.validate import validate_response
from research_agent.config import get_temperature, get_max_output_tokens
from research_agent.trace_log import set_trace_id, logger

MAX_ROUNDS = int(os.environ.get("RESEARCH_AGENT_MAX_ROUNDS", "50"))
MAX_TOTAL_RETRIES = 5
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
    """Detect a task still awaiting the user (waiting-for-human state).

    Returns a structured PendingTask with a trimmed actionable description
    (strips lead-in phrases like "需要你/请你/你来"), instead of a raw
    response tail.
    """
    indicators = [
        "需要你", "请你", "你来", "你自己", "手动", "等待你",
        "等你", "你来做", "需要你完成", "需要实验", "需要您", "麻烦你",
    ]
    response = (response or "").strip()
    for ind in indicators:
        idx = response.find(ind)
        if idx != -1:
            desc = response[idx:]
            # Trim trailing punctuation and cap at the first sentence end.
            import re as _re
            m = _re.search(r"[。！？.!?]", desc[1:])
            if m:
                desc = desc[: m.start() + 2]
            desc = desc.strip("，,。;； ")
            return PendingTask(description=desc[:200], expected_time="")
    return None


def _usage_dict(u) -> dict:
    """Normalize a litellm usage object, PRESERVING provider cache fields
    (deepseek reports prompt_cache_hit_tokens / prompt_tokens_details.cached_tokens)."""
    d = {"prompt_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
         "completion_tokens": int(getattr(u, "completion_tokens", 0) or 0)}
    hit = getattr(u, "prompt_cache_hit_tokens", None)
    if hit is not None:
        d["prompt_cache_hit_tokens"] = int(hit)
    details = getattr(u, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details is not None else None
    if cached is not None:
        d["prompt_tokens_details"] = {"cached_tokens": int(cached)}
    return d


def _parse_json_flex(raw: str):
    text = re.sub(r'^```(?:json)?\s*\n?', '', raw.strip())
    text = re.sub(r'\n?```\s*$', '', text)
    return json.loads(text.strip())


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
    # Additive telemetry fields — the kernel ignores them; the metered runtime
    # (see telemetry.MeteredRuntime) reads them for usage accounting.
    u = getattr(resp, "usage", None)
    if u is not None:
        result["usage"] = _usage_dict(u)
    result["model"] = model
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
    t0 = time.monotonic()
    try:
        resp = litellm.completion(**litellm_kw, stream_options={"include_usage": True})
    except Exception:
        resp = litellm.completion(**litellm_kw)
    content = ""
    usage = None
    for chunk in resp:
        u = getattr(chunk, "usage", None)
        if u is not None:
            usage = u
        delta = chunk.choices[0].delta
        if delta.content:
            content += delta.content
            _emit(emit, "reply", {"text": delta.content})
    # Emit usage through the existing event channel (observed, never in the loop).
    if emit is not None and usage is not None:
        payload = {"model": model,
                   "latency_ms": round((time.monotonic() - t0) * 1000, 1),
                   "purpose": "answer"}
        payload.update(_usage_dict(usage))
        _emit(emit, "llm_usage", payload)
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
                cmd, capture_output=True, text=True, errors="replace", timeout=30,
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


def _tool_approval_hook(state, tool_name: str, params: dict, emit) -> str | None:
    """Host-injected pre-dispatch policy: guardrail + HITL approval.

    Called by the runtime before any tool dispatch (runtime is tool-agnostic).
    Returns None to allow, or a block reason to reject. Two concerns:
      - shell_exec: deterministic guardrail, HITL only when a danger pattern hits;
      - any tool whose schema declares requires_approval (e.g. self-evolution
        writes): always HITL-confirmed. The host knows tool semantics, not the kernel.
    """
    if tool_name == "shell_exec":
        from research_agent.guardrail import guardrail as g_rail
        from research_agent.models import Action
        import uuid as _uuid
        block_reason = g_rail(Action(action=tool_name, query=params.get("command", "")))
        if not block_reason:
            return None
        confirm_id = str(_uuid.uuid4())[:8]
        command_text = params.get("command", "")
        _emit(emit, "confirm_required", {
            "id": confirm_id, "tool": tool_name,
            "command": command_text[:200], "reason": block_reason})
        state._pending_confirms[confirm_id] = {"event": threading.Event(), "approved": False}
        state._pending_confirms[confirm_id]["event"].wait(timeout=60)
        confirmed = state._pending_confirms.pop(confirm_id, {}).get("approved", False)
        return None if confirmed else f"User cancelled: {block_reason}"

    from research_agent.tools import get_registry
    tool = get_registry().tools.get(tool_name)
    if tool is not None and getattr(tool, "requires_approval", False):
        import uuid as _uuid
        confirm_id = str(_uuid.uuid4())[:8]
        reason = f"{tool_name}: 写入/修改状态的操作，需要用户确认"
        _emit(emit, "confirm_required", {
            "id": confirm_id, "tool": tool_name, "reason": reason})
        state._pending_confirms[confirm_id] = {"event": threading.Event(), "approved": False}
        state._pending_confirms[confirm_id]["event"].wait(timeout=60)
        confirmed = state._pending_confirms.pop(confirm_id, {}).get("approved", False)
        return None if confirmed else f"User cancelled: {tool_name} requires approval"
    return None


def run_agent(user_input: str, llm: LLMProvider, state: AgentState,
              on_event: EventCallback = None,
              workspace_dir: str = "", chat_id: str = "") -> AgentState:
    set_trace_id()
    logger.info(f"run_agent START: {user_input[:80]}")
    try:
        state._memory_retrievals = []  # reset per-turn retrieval loop guard
    except Exception:
        pass

    # ── Diagnostics: record every event + run fault monitor (side-channel) ──
    from research_agent.tools import is_plugin_enabled as is_enabled
    _diag_on = is_enabled("diagnostics")
    from research_agent.diagnostics.recorder import EventRecorder
    from research_agent.diagnostics.monitor import RunMonitor
    from research_agent.trace_log import get_trace_id
    recorder = EventRecorder(trace_id=get_trace_id(),
                             workspace_dir=workspace_dir, chat_id=chat_id,
                             enabled=_diag_on)
    monitor = RunMonitor()
    _diag_lock = threading.Lock()
    _faults_emitted: set[tuple] = set()

    def _diag_fault(fault: dict):
        if not _diag_on:
            return
        key = (fault.get("kind"), fault.get("tool"))
        with _diag_lock:
            if key in _faults_emitted:
                return
            _faults_emitted.add(key)
        recorder.record("fault", fault)
        if on_event:
            try:
                on_event("fault", dict(fault))
            except Exception:
                pass

    _changed_paths: set[str] = set()

    def _on_event_inner(et: str, d: dict):
        if _diag_on:
            recorder.record(et, d)
            monitor.observe(et, d)
        if et == "file_change" and d.get("path"):
            _changed_paths.add(d["path"])

    if on_event is not None:
        _orig_on_event = on_event

        def on_event(et: str, d: dict):
            _on_event_inner(et, d)
            _orig_on_event(et, d)
    else:
        on_event = _on_event_inner

    from research_agent.tools import get_registry
    from research_agent.tools.builtin import register_builtins
    register_builtins()

    monitor.observe("start", {})
    recorder.record("start", {"input": user_input})

    def _finish_run():
        recorder.record("end", {"final_response": getattr(state, "final_response", "")})
        for fault in monitor.finalize(getattr(state, "final_response", "")):
            _diag_fault(fault)

    state.workspace_dir = workspace_dir
    state.active_chat_id = chat_id
    state.sections = []  # track structured sections for persistence

    # Auto-load MCP servers from config (feature-gated: skip entirely when disabled)
    if os.environ.get("RESEARCH_AGENT_MCP", "1") == "1":
        try:
            from research_agent.tools import is_plugin_enabled as is_enabled
            if is_enabled("mcp"):
                from research_agent.tools.mcp_loader import MCPManager, default_config_path
                mcp_config = default_config_path()
                if os.path.exists(mcp_config):
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

    # ── Ensure a workspace/project binding (clean default when none given) ──
    from research_agent import project_manager as pm
    if not workspace_dir:
        from research_agent.config import get_data_dir
        workspace_dir = str(get_data_dir() / "workspaces" / "default")
    if not pm.is_project_dir(workspace_dir):
        import os as _os
        _os.makedirs(workspace_dir, exist_ok=True)
        label = _os.path.basename(_os.path.normpath(workspace_dir)) or "default"
        proj = pm.init_project(workspace_dir, topic=label)
        try:
            from research_agent.tools.git_tool import git_init
            git_init(workspace_dir)
        except Exception:
            pass
    else:
        proj = pm.load_project(workspace_dir)

    state.active_project = Project(
        id=pm.get_project_id(workspace_dir),
        topic=(proj or {}).get("topic", "默认项目"),
        status=ProjectStatus.ACTIVE,
        workspace_dir=workspace_dir,
        created_at=(proj or {}).get("created_at", datetime.now().isoformat()),
        updated_at=datetime.now().isoformat(),
    )

    state.conversation_turns = get_recent_turns(workspace_dir, chat_id, limit=20)

    tools_list = registry.list_for_llm()
    # ── Run the (replaceable) agent kernel ──
    from research_agent.runtime import RuntimeContext, FunctionCallingRuntime

    def _call_tools_patchable(llm, messages, tools, tool_choice="auto"):
        # resolve current module attr so tests' patch('...agent._call_llm_with_tools') applies
        import research_agent.agent as _ag
        return _ag._call_llm_with_tools(llm, messages, tools, tool_choice)

    def _stream_patchable(llm, messages, emit):
        import research_agent.agent as _ag
        return _ag._stream_response(llm, messages, emit)

    ctx = RuntimeContext(
        llm=llm,
        user_input=user_input,
        state=state,
        registry=registry,
        workspace_dir=workspace_dir,
        chat_id=chat_id,
        emit=on_event,
        pre_tool_hook=_tool_approval_hook,
        on_tool_success=_auto_validate,
        max_rounds=MAX_ROUNDS,
        call_llm_with_tools=_call_tools_patchable,
        stream_response=_stream_patchable,
    )
    base_runtime = FunctionCallingRuntime()
    runtime = base_runtime
    if is_enabled("telemetry"):
        try:
            from research_agent.telemetry import MeteredRuntime
            runtime = MeteredRuntime(base_runtime)   # observation only; loop untouched
        except Exception:
            runtime = base_runtime
    runtime.run(ctx)
    # ── Collect file changes as a keep/undo proposal (git-based) ──
    try:
        from research_agent.proposal import ProposalManager
        pmgr = ProposalManager(workspace_dir)
        if _changed_paths and pmgr.is_repo():
            proposals = pmgr.collect(_changed_paths)
            state.pending_proposals = proposals
            for c in proposals:
                _emit(on_event, "proposal", {
                    "path": c.path, "status": c.status,
                    "additions": c.additions, "deletions": c.deletions})
    except Exception:
        pass

    # ── Stream final response ──
    state = validate_response(state)
    _save_turn(state, workspace_dir, chat_id)
    _maybe_compress(workspace_dir, chat_id, llm)
    _maybe_distill(state, workspace_dir, chat_id, llm)
    _mark_waiting_if_needed(state)

    # ── Semantic self-eval (opt-in, one cheap LLM call at run end) ──
    if os.environ.get("RESEARCH_AGENT_SEMANTIC_CHECK", "0") == "1":
        try:
            from research_agent.validate import run_semantic_check
            issues = run_semantic_check(state, llm=llm)
            for iss in issues:
                recorder.record("semantic_issue", iss)
                if on_event:
                    try:
                        on_event("semantic_issue", dict(iss))
                    except Exception:
                        pass
        except Exception:
            pass

    _finish_run()
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
        from research_agent.tools import is_plugin_enabled as is_enabled
        from research_agent.config import get_memory_config
        mem_cfg = get_memory_config()
        if (not is_enabled("memory") or not mem_cfg.get("enabled", True)
                or not mem_cfg.get("distill", True)):
            return
        from research_agent.memory import source as mem_source
        from research_agent.memory import pipeline as mem_pipeline
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


def _turns_token_count(turns) -> int:
    """Estimate tokens of a list of conversation turns."""
    from research_agent.context import count_tokens
    try:
        return sum(
            count_tokens((t.user_message or "") + "\n" + (t.assistant_message or ""))
            for t in turns
        )
    except Exception:
        return sum(len(t.user_message or "") + len(t.assistant_message or "") for t in turns)


def _compress_budget() -> int:
    """Uncompressed-history token budget before compression kicks in.

    Derived from the model's context window (context.compress_ratio, default
    0.6) instead of a flat 12000, so compression only fires when history actually
    approaches the window. RESEARCH_AGENT_COMPRESS_TOKENS overrides everything.
    """
    env = os.environ.get("RESEARCH_AGENT_COMPRESS_TOKENS", "")
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    try:
        from research_agent.config import (
            get_context_config, get_max_context_tokens, get_model_name)
        ratio = get_context_config().get("compress_ratio", 0.6)
        window = get_max_context_tokens(get_model_name())
    except Exception:
        ratio, window = 0.6, 0
    if not window:
        return 10 ** 9  # unknown window → never auto-compress on size
    return max(2000, int(window * ratio))


def _maybe_compress(workspace_dir: str, chat_id: str, llm: LLMProvider):
    """Compress old turns once uncompressed history exceeds a token budget.

    Budget-based (instead of the old hardcoded ">10 turns") so short/long
    messages trigger compression at comparable cost. Recent 5 turns are always
    kept verbatim; older turns are summarized into conclusions/dead_ends and
    appended to progress.md.
    """
    try:
        from research_agent.config import get_context_config
        if not get_context_config().get("compress_enabled", True):
            return
    except Exception:
        pass
    uncompressed = count_uncompressed_turns(workspace_dir, chat_id)
    if uncompressed <= 6:
        return
    all_turns = get_recent_turns(workspace_dir, chat_id, limit=uncompressed)
    keep_recent = 5
    old_turns = all_turns[:-keep_recent] if len(all_turns) > keep_recent else []
    if not old_turns:
        return
    if _turns_token_count(all_turns) < _compress_budget():
        return

    turns_text = "\n".join([f"用户: {t.user_message}\n助手: {t.assistant_message}" for t in old_turns])
    summary = llm.complete(
        [{"role": "user", "content": 
            f"将以下对话压缩为摘要，分两个字段输出JSON：\n"
            f"1. conclusions: 关键决策、数据、已确认结论（1-2句）\n"
            f"2. dead_ends: 尝试过但不可行的方向、被推翻的假设、已验证不可行的方法（保留这些很重要，避免重复犯错）\n"
            f"输出JSON: {{\"conclusions\": \"...\", \"dead_ends\": \"...\"}}\n"
            f"对话:\n{turns_text}"}],
        max_tokens=200, purpose="compress"
    )
    indices = [i for i, t in enumerate(old_turns) if t.id]
    mark_compressed(workspace_dir, chat_id, indices, summary)

    try:
        from research_agent import project_manager as pm
        existing_progress = pm.load_progress(workspace_dir)
        progress_prompt = f"基于以下对话，用一句话总结当前项目进度（已完成什么、正在做什么、下一步做什么）:\n{turns_text}"
        progress = llm.complete([{"role": "user", "content": progress_prompt}],
                                max_tokens=100, purpose="compress")
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
            _persist_pending_task(state, task)


def _persist_pending_task(state: AgentState, task: PendingTask):
    """Persist an awaiting-user task into Tier B memory (kind=task).

    Non-blocking / best-effort; guards against storing task descriptions that
    are just conversational filler (e.g. generic offers without an action).
    """
    try:
        from research_agent.tools import is_plugin_enabled as is_enabled
        from research_agent.config import get_memory_config
        if not is_enabled("memory") or not get_memory_config().get("enabled", True):
            return
        from research_agent.memory.models import MemoryUnit, MemoryKind, MemoryScope
        from research_agent.memory.tier_b import get_manager
        desc = (task.description or "").strip()
        if len(desc) < 4:
            return
        src = {}
        if state.active_project and getattr(state.active_project, "id", None):
            src["project_id"] = state.active_project.id
        if getattr(state, "workspace_dir", ""):
            src["workspace_dir"] = state.workspace_dir
        if task.expected_time:
            src["expected_time"] = task.expected_time
        unit = MemoryUnit(
            text=f"待办: {desc}",
            kind=MemoryKind.TASK,
            importance=0.7,
            scope=MemoryScope.USER,
            source=src,
        )
        get_manager().write(unit)
    except Exception:
        pass


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
        state.final_response = ""
        state.error = ""
    return process_user_input(state, thread_id=thread_id)

