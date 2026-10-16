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
from research_agent.kernel import AgentKernel

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
    from research_agent.model_retry import retry_model
    return retry_model(llm_func, emit, max_retries)


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
    from research_agent.model_adapter import call_with_tools
    return call_with_tools(llm, messages, tools, tool_choice)


def _stream_response(llm: LLMProvider, messages: list[dict], emit: EventCallback):
    """Stream LLM response token-by-token with retry."""
    from research_agent.model_retry import stream_with_retry
    return stream_with_retry(lambda callback: _stream_response_once(llm, messages, callback), emit)


def _stream_response_once(llm: LLMProvider, messages: list[dict], emit: EventCallback):
    from research_agent.model_adapter import stream_once
    return stream_once(llm, messages, emit)


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


def _wait_for_approval(event, timeout=60, cancellation=None):
    import time
    deadline = time.monotonic() + timeout
    while True:
        if cancellation is not None:
            cancellation.check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if event.wait(min(0.05, remaining)):
            if cancellation is not None:
                cancellation.check()
            return True


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
        try:
            _wait_for_approval(state._pending_confirms[confirm_id]["event"], cancellation=getattr(state, "_cancellation", None))
            confirmed = state._pending_confirms[confirm_id]["approved"]
        finally:
            state._pending_confirms.pop(confirm_id, None)
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
        try:
            _wait_for_approval(state._pending_confirms[confirm_id]["event"], cancellation=getattr(state, "_cancellation", None))
            confirmed = state._pending_confirms[confirm_id]["approved"]
        finally:
            state._pending_confirms.pop(confirm_id, None)
        return None if confirmed else f"User cancelled: {tool_name} requires approval"
    return None


def run_agent_resilient(user_input, llm, state, on_event=None, *, recovery_delays=(2, 5), **options):
    """Bounded, cancellable host recovery using one durable execution identity."""
    import uuid
    from research_agent.execution import CancellationToken, Cancelled
    token = options.get("cancellation") or CancellationToken()
    options["cancellation"] = token
    options["execution_id"] = options.get("execution_id") or uuid.uuid4().hex
    for attempt in range(len(recovery_delays) + 1):
        try:
            result = run_agent(user_input, llm, state, on_event=on_event, **options)
        except RuntimeError as exc:
            if not attempt or str(exc) != "Workspace or conversation already has an active request":
                raise
            _emit(on_event, "network_wait", {"execution_id": options["execution_id"],
                  "text": "工作区仍在执行，已保存的任务等待稍后恢复"})
            return state
        if result.termination_reason != "waiting_network" or attempt == len(recovery_delays):
            return result
        # Cancellation remains available while no registry request is active.
        state._cancellation = token
        _emit(on_event, "network_wait", {"execution_id": options["execution_id"],
              "seconds": recovery_delays[attempt], "attempt": attempt + 1,
              "text": "进度已保存，等待网络恢复后继续"})
        try:
            token.wait(recovery_delays[attempt])
        except Cancelled:
            from research_agent.execution import cancel_checkpoint
            cancel_checkpoint(options["execution_id"], only_waiting=True)
            state.termination_reason = "cancelled"
            state.error = "Request cancelled; saved progress and completed effects are retained"
            return state


def run_agent(user_input, llm, state, on_event=None, workspace_dir="", chat_id="", *,
              kernel=None, cancellation=None, execution_id=None, isolate_session=None):
    from research_agent.execution import exclusive_request, CancellationToken, Cancelled, RecoveryRequired
    from research_agent.context_budget import ContextBudgetExceeded
    import uuid
    from research_agent.model_retry import ModelRequestRejected, PartialResponseError, ModelUnavailable, retryable
    from research_agent.docker_ownership import reconcile_before_request
    recovered = reconcile_before_request()
    if recovered:
        _emit(on_event, "sandbox_recovered", {"containers": recovered})
    if not workspace_dir:
        from research_agent.config import get_data_dir
        workspace_dir = str(get_data_dir() / "workspaces" / "default")
    from research_agent.session_workspace import resolve_session, project_workspace, bound_session
    state.project_workspace_dir = str(project_workspace(workspace_dir))
    state.session_branch = ""
    binding = resolve_session(workspace_dir, chat_id, isolate_session)
    if binding is not None:
        from research_agent.project_manager import ensure_chat
        ensure_chat(binding.project, chat_id)
        workspace_dir = binding.workspace
        state.project_workspace_dir = binding.project
        state.session_branch = binding.branch
        _emit(on_event, "session_workspace", {"project_workspace": binding.project,
              "workspace": binding.workspace, "branch": binding.branch, "chat_id": chat_id})
    from research_agent.tools import get_registry
    registry = get_registry()
    registry.background_tasks.recover_workspace(workspace_dir)
    from research_agent.checkpoint_events import consume_workspace, foreground_finished
    consume_workspace(workspace_dir)
    with exclusive_request(workspace_dir, chat_id) as lease:
        # A concurrent first binding must not let an already-resolved shared request enter.
        if bound_session(chat_id) != binding:
            raise RuntimeError("Session binding changed during admission; retry request")
        state._request_lease = lease
        state._cancellation = cancellation if cancellation is not None else CancellationToken()
        state._execution_id = execution_id or uuid.uuid4().hex
        snapshot_safe = False
        suspended = False
        try:
            state._cancellation.check()
            with registry.request_scope(chat_id, state._cancellation) as session_token:
                state._cancellation = session_token
                result = _run_agent_impl(user_input, llm, state, on_event, workspace_dir, chat_id, kernel=kernel)
            snapshot_safe = True
            return result
        except (ModelUnavailable, PartialResponseError) as exc:
            partial = getattr(exc, "partial", "")
            if isinstance(exc, ModelUnavailable) or retryable(exc.cause):
                from research_agent.network_recovery import suspend_execution
                try:
                    suspend_execution(state, user_input, workspace_dir, chat_id, exc, partial=partial)
                except RecoveryRequired as failure:
                    state.termination_reason = "recovery_required"
                    state.error = str(failure)
                    return state
                suspended = True
                state.termination_reason = "waiting_network"
                state.final_response = partial
                state.error = ""
                _emit(on_event, "execution_suspended", {"execution_id": state._execution_id,
                      "status": "waiting_network", "partial": partial,
                      "text": "网络暂时不可用，进度已保存，可从中断步骤继续"})
                return state
            state.termination_reason = "partial_response"
            state.final_response = exc.partial
            state.error = str(exc)
            snapshot_safe = True
            return state
        except ModelRequestRejected as exc:
            state.termination_reason = "model_error"
            state.error = str(exc)
            snapshot_safe = True
            return state
        except ContextBudgetExceeded as exc:
            state.termination_reason = "context_limit"
            state.error = str(exc)
            snapshot_safe = True
            return state
        except RecoveryRequired as exc:
            state.termination_reason = "recovery_required"
            state.error = str(exc)
            return state
        except Cancelled:
            from research_agent.execution import cancel_checkpoint
            cancel_checkpoint(state._execution_id)
            state.termination_reason = "cancelled"
            state.error = "Request cancelled; completed side effects are not undone"
            snapshot_safe = True
            return state
        finally:
            if suspended:
                from research_agent.checkpoint_events import foreground_suspended
                foreground_suspended(lease)
            else:
                foreground_finished(lease, safe=snapshot_safe)


def _run_agent_impl(user_input: str, llm: LLMProvider, state: AgentState,
              on_event: EventCallback = None,
              workspace_dir: str = "", chat_id: str = "", *,
              kernel: AgentKernel | None = None) -> AgentState:
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
    _tool_path: list[str] = []

    def _on_event_inner(et: str, d: dict):
        if _diag_on:
            recorder.record(et, d)
            monitor.observe(et, d)
        if et == "file_change" and d.get("path"):
            _changed_paths.add(d["path"])
        if et == "tool_start" and d.get("name"):
            _tool_path.append(d["name"])

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
    state._last_checkpoint = ""
    state.sections = []  # track structured sections for persistence
    state.caps.pop("plan", None)  # discard compatibility data from a previous task

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
    registry.bind_state(state)
    component_path = _component_state_path(workspace_dir, chat_id)
    if component_path.exists():
        state.components.restore(component_path, lifetimes={"application", "session"})
    # New tasks start empty; resume restores task fields from the execution checkpoint.
    state.components.reset(lifetimes={"request", "task"}, exclude_owners={"core"})

    state.user_input = user_input

    # ── Ensure a workspace/project binding (clean default when none given) ──
    from research_agent import project_manager as pm
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

    if is_enabled("plan"):
        from research_agent.plan_pipeline import recover_pending
        recover_pending(llm=llm)
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
    from research_agent.proposal import ProposalManager
    ProposalManager(workspace_dir).begin_execution()
    base_runtime = FunctionCallingRuntime(kernel=kernel)
    runtime = base_runtime
    if is_enabled("telemetry"):
        try:
            from research_agent.telemetry import MeteredRuntime
            runtime = MeteredRuntime(base_runtime)   # observation only; loop untouched
        except Exception:
            runtime = base_runtime
    # Resume admission must precede creation/retirement of workspace snapshots.
    from research_agent.network_recovery import prepare_execution
    prepare_execution(ctx, base_runtime.kernel.id)

    try:
        runtime.run(ctx)
    finally:
        runtime_failure = sys.exc_info()[1]
        # Close the run-scoped (ephemeral) sandbox session, if one was opened.
        try:
            from research_agent.sandbox import end_run as _sandbox_end_run
            _sandbox_end_run(state)
        except Exception as exc:
            from research_agent.execution import RecoveryRequired
            raise RecoveryRequired(f"Sandbox cleanup incomplete: {exc}") from exc
        if runtime_failure is not None:
            from research_agent.execution import RecoveryRequired
            from research_agent.model_retry import ModelUnavailable, PartialResponseError
            checkpoint_ref = getattr(state, "_last_checkpoint", "")
            if checkpoint_ref and not isinstance(runtime_failure, (RecoveryRequired, ModelUnavailable, PartialResponseError)):
                from research_agent.checkpoint import seal_checkpoint_if_idle
                seal_checkpoint_if_idle(workspace_dir, checkpoint_ref)
    checkpoint_ref = getattr(state, "_last_checkpoint", "")
    if checkpoint_ref:
        from research_agent.checkpoint import seal_checkpoint_if_idle
        seal_checkpoint_if_idle(workspace_dir, checkpoint_ref)
    # ── Collect file changes as a keep/undo proposal (git-based) ──
    try:
        from research_agent.proposal import ProposalManager
        pmgr = ProposalManager(workspace_dir)
        proposal_paths = _changed_paths or list(pmgr._reviewed)
        if pmgr.is_repo():
            proposals = pmgr.collect(None if checkpoint_ref else proposal_paths, _host_locked=True)
            state.pending_proposals = proposals
            for c in proposals:
                _emit(on_event, "proposal", {
                    "path": c.path, "status": c.status,
                    "additions": c.additions, "deletions": c.deletions})
    except Exception:
        pass

    # ── Stream final response ──
    checkpoint_ref = getattr(state, "_last_checkpoint", "")
    if checkpoint_ref:
        from research_agent.checkpoint import seal_checkpoint_if_idle
        seal_checkpoint_if_idle(workspace_dir, checkpoint_ref)
    state = validate_response(state)
    _save_turn(state, workspace_dir, chat_id)
    _maybe_save_episode(state, workspace_dir, chat_id, llm=llm, tool_path=_tool_path)
    from research_agent.execution import execute_once
    from research_agent.tools.schema import ToolResult
    def compress_once():
        _maybe_compress(workspace_dir, chat_id, llm)
        return ToolResult.ok()
    execute_once(state._execution_id, "post:compress", "conversation_compression", {}, compress_once)
    _mark_waiting_if_needed(state)

    # ── Semantic self-eval (opt-in, one cheap LLM call at run end) ──
    if os.environ.get("RESEARCH_AGENT_SEMANTIC_CHECK", "0") == "1":
        try:
            from research_agent.validate import run_semantic_check
            judged = execute_once(state._execution_id, "post:semantic", "semantic_check", {},
                                  lambda: ToolResult.ok(issues=run_semantic_check(state, llm=llm)))
            issues = judged.data["issues"]
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


def _component_state_path(workspace_dir, chat_id):
    import hashlib
    from research_agent.config import get_data_dir
    identity = hashlib.sha256((workspace_dir + "\0" + chat_id).encode()).hexdigest()
    return get_data_dir() / "state" / (identity + ".json")


def _save_turn(state: AgentState, workspace_dir: str, chat_id: str):
    state.components.save(_component_state_path(workspace_dir, chat_id), lifetimes={"application", "session"})
    round_num = len(state.conversation_turns) + 1 if hasattr(state, 'conversation_turns') else 1
    store_turn(workspace_dir, chat_id, round_num, state.user_input, state.final_response or "",
               sections=getattr(state, 'sections', None),
               execution_id=getattr(state, '_execution_id', None))


def _maybe_save_episode(state: AgentState, workspace_dir: str, chat_id: str,
                        llm=None, tool_path: list[str] | None = None):
    """Automatic task-episode write at run end (async).

    - Explicit plan (plan_update) → saved synchronously (goal/steps/decisions/
      outcome), enriched with the tools actually used.
    - Otherwise, if the run used any tools, an episode is distilled OFF the
      request thread (small model when `plan.distill`, else heuristic fallback).

    Stored under the project data dir (never the git workspace). Auto path gated
    by `plan.auto_episode` (default true); explicit plans are always saved.
    """
    try:
        from research_agent.tools import is_plugin_enabled as is_enabled
        if not is_enabled("plan"):
            return
        from research_agent import plan as plan_mod
        from research_agent.plan_state import current_plan
        current = current_plan(state)
        raw = current.to_dict() if current is not None else None
        tools = list(dict.fromkeys(tool_path or []))

        if raw:
            plan = plan_mod.Plan.from_dict(raw)
            plan.chat_id = chat_id or plan.chat_id
            plan.workspace_dir = workspace_dir or plan.workspace_dir
            plan.tools_used = list(dict.fromkeys(list(plan.tools_used) + tools))
            plan_mod.save_plan(plan)
            return

        # No explicit plan: auto-record only when the run actually did work.
        use_llm = True
        try:
            from research_agent.config import load_config
            pcfg = load_config().get("plan") or {}
            if not bool(pcfg.get("auto_episode", True)):
                return
            use_llm = bool(pcfg.get("distill", True))
        except Exception:
            pass
        if not tools:
            return

        from research_agent import plan_pipeline
        plan_pipeline.submit_run(
            user_input=getattr(state, "user_input", ""),
            final_response=getattr(state, "final_response", ""),
            tool_path=tools, workspace_dir=workspace_dir, chat_id=chat_id,
            llm=llm, use_llm=use_llm and llm is not None,
            execution_id=getattr(state, "_execution_id", None))
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

