"""Runtime host and compatibility adapters around the replaceable AgentKernel.

AgentKernel owns the thinking loop. RuntimeHost supplies context, model calls,
tool dispatch, approval hooks, retries and checkpoints through KernelHost ports.
agent.run_agent owns application assembly and request lifecycle. AgentRuntime
and FunctionCallingRuntime remain compatibility entry points; a new thinking
strategy implements AgentKernel and is installed through kernel_factory.
"""
import json
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Protocol

from research_agent.kernel import (AgentKernel, FunctionCallingKernel, KernelRequest,
                                   ModelResponse, ToolCall, ToolObservation)
from research_agent.tools.schema import ToolResult
from research_agent.model_adapter import ModelAdapter

from research_agent.models import AgentState
from research_agent.llm import LLMProvider
from research_agent.config import get_temperature, get_max_output_tokens

MAX_ROUNDS = int(os.environ.get("RESEARCH_AGENT_MAX_ROUNDS", "50"))
MAX_TOTAL_RETRIES = 5
LLM_RETRY_BACKOFF = [1, 2, 4]

EventCallback = Callable[[str, dict], None]


class ToolRegistryPort(Protocol):
    def generate_capabilities(self) -> str: ...
    def list_for_llm(self) -> list[dict]: ...
    def dispatch(self, name: str, params: dict, llm: LLMProvider,
                 state: AgentState, emit: EventCallback | None) -> ToolResult: ...


ApprovalHook = Callable[[AgentState, str, dict, EventCallback | None], str | None]
SuccessHook = Callable[[AgentState, str, dict, list[dict], EventCallback | None], None]
ModelCall = Callable[[LLMProvider, list[dict], list[dict], str], dict]
AnswerCall = Callable[[LLMProvider, list[dict], EventCallback | None], str]


@dataclass
class RuntimeContext:
    """Everything a strategy needs to execute one agent request."""
    llm: LLMProvider
    user_input: str
    state: AgentState
    registry: ToolRegistryPort | None = None          # ToolRegistry (dispatch + capabilities)
    workspace_dir: str = ""
    chat_id: str = ""
    emit: EventCallback | None = None
    # Called before a tool is dispatched. Signature:
    #   pre_tool_hook(state, tool_name, params, emit) -> str | None
    # Return an approval/block reason string to reject the call (and the caller
    # feeds it back to the model), or None to allow. The kernel does NOT know any
    # specific tool name — dangerous-action policy is entirely host-injected.
    pre_tool_hook: ApprovalHook | None = None
    # Called after a successful file_write/file_edit (host hook for validation).
    on_tool_success: SuccessHook | None = None
    max_rounds: int = MAX_ROUNDS
    # Injectable LLM-call primitives. The host provides wrappers that resolve the
    # patched names at call time (keeps `patch('research_agent.agent._call_llm
    # _with_tools')` working in tests). Defaults to the runtime-local functions.
    call_llm_with_tools: ModelCall | None = None
    stream_response: AnswerCall | None = None
    model_adapter: ModelAdapter | None = None
    token_counter: Callable[[list[dict], list[dict]], int] | None = None
    output_limit: int | None = None


def _emit(event: EventCallback | None, event_type: str, data: dict):
    if event:
        event(event_type, data)


from research_agent.kernel import _ProgressGuard, _attach_hint  # compatibility exports


def _budgeted_call(ctx, invoke):
    from research_agent.context_budget import output_cap
    from research_agent.execution import cancellable_call
    token = output_cap.set(ctx.output_limit)
    from research_agent.model_retry import retry_wait
    cancellation = getattr(ctx.state, "_cancellation", None)
    waiting = retry_wait.set(cancellation.wait if cancellation is not None else None)
    try:
        return cancellable_call(invoke, ctx.state)
    finally:
        retry_wait.reset(waiting)
        output_cap.reset(token)


def _default_call_llm(ctx: RuntimeContext, messages, tools):
    token = getattr(ctx.state, "_cancellation", None)
    if token is not None:
        token.check()
    if ctx.model_adapter is not None:
        from research_agent.execution import cancellable_call
        return _budgeted_call(ctx, lambda: ctx.model_adapter.select(messages, tools, "auto"))
    fn = ctx.call_llm_with_tools or _call_llm_with_tools
    from research_agent.execution import cancellable_call
    return _budgeted_call(ctx, lambda: fn(ctx.llm, messages, tools, "auto"))


def _default_stream(ctx: RuntimeContext, messages):
    token = getattr(ctx.state, "_cancellation", None)
    def emit(kind, data):
        if token is not None:
            token.check()
        _emit(ctx.emit, kind, data)
    if ctx.model_adapter is not None:
        from research_agent.execution import cancellable_call
        from research_agent.model_retry import stream_with_retry
        return _budgeted_call(ctx, lambda: stream_with_retry(lambda callback: ctx.model_adapter.answer(messages, callback), emit, MAX_TOTAL_RETRIES))
    fn = ctx.stream_response or _stream_response
    from research_agent.execution import cancellable_call
    return _budgeted_call(ctx, lambda: fn(ctx.llm, messages, emit))


def _parse_json_flex(raw: str):
    text = re.sub(r'^```(?:json)?\s*\n?', '', raw.strip())
    text = re.sub(r'\n?```\s*$', '', text)
    return json.loads(text.strip())


def _call_llm_with_tools(llm: LLMProvider, messages: list[dict],
                         tools: list[dict], tool_choice: str = "auto") -> dict:
    from research_agent.model_adapter import call_with_tools
    return call_with_tools(llm, messages, tools, tool_choice)


def _stream_response_once(llm: LLMProvider, messages: list[dict], emit: EventCallback):
    from research_agent.model_adapter import stream_once
    return stream_once(llm, messages, emit)


def _stream_response(llm: LLMProvider, messages: list[dict], emit: EventCallback) -> str:
    """Stream LLM response token-by-token with retry."""
    from research_agent.model_retry import stream_with_retry
    return stream_with_retry(lambda callback: _stream_response_once(llm, messages, callback), emit, MAX_TOTAL_RETRIES)


def _generate_msgs(messages: list[dict], state) -> list[dict]:
    """Build a plain (no tools) message tail with tool results for final answer."""
    from research_agent.context import user_content_with_attachments
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    results = []
    for i, tm in enumerate(tool_msgs):
        content = tm.get("content", "")[:6000]
        results.append({"role": "system", "content": f"[工具结果 {i+1}]\n{content}"})
    return [
        {"role": "system", "content": "=== 工具调用结果 ==="},
        *results,
        {"role": "system", "content": "=== 工具结果结束，回答用户问题 ==="},
        {"role": "user", "content": user_content_with_attachments(state)},
    ]


class AgentRuntime(ABC):
    """Strategy contract for turning a request into a final answer."""

    id: str = "base"

    @abstractmethod
    def run(self, ctx: RuntimeContext) -> AgentState:
        """Execute one agent turn. Fills state.final_response; returns state."""


class RuntimeHost:
    """Interaction adapter. Kernels only see the KernelHost port."""

    def __init__(self, ctx: RuntimeContext):
        self.ctx = ctx
        self._token_overhead = 0
        self._last_budget = None

    def checkpoint(self, payload=None):
        if payload is not None and "messages" in payload:
            from research_agent.tool_context import compact_completed
            compact_completed(payload["messages"], self.ctx.registry, self.ctx.state)
        run_id = getattr(self.ctx.state, "_execution_id", None)
        if not run_id:
            return None
        import json
        from research_agent.execution import checkpoint, serializable_caps, restore_execution_state
        identity = json.dumps([self.ctx.user_input, self.ctx.workspace_dir, self.ctx.chat_id,
                               getattr(self.ctx.state, "_kernel_id", "")])
        if payload is not None:
            payload = dict(payload, caps=serializable_caps(self.ctx.state.caps))
            payload["fields"] = self.ctx.state.components.snapshot()
            payload["workspace_checkpoint"] = getattr(self.ctx.state, "_last_checkpoint", "")
            payload["host_budget"] = {"token_overhead": self._token_overhead,
                                      "output_limit": self.ctx.output_limit}
            if self.ctx.workspace_dir:
                from research_agent import checkpoint as cp
                if cp.is_repo(self.ctx.workspace_dir):
                    payload["workspace_state"] = cp._state(self.ctx.workspace_dir)
        restored = checkpoint(run_id, identity, payload)
        if payload is None and restored:
            restore_execution_state(self.ctx.state, restored)
            if restored.get("workspace_checkpoint"):
                self.ctx.state._last_checkpoint = restored["workspace_checkpoint"]
            self._token_overhead = restored.get("host_budget", {}).get("token_overhead", 0)
            self.ctx.output_limit = restored.get("host_budget", {}).get("output_limit")
            from research_agent.tool_context import append_probe_notices
            if append_probe_notices(restored["messages"], self.ctx.registry, self.ctx.state):
                # Recovery admission is a fixed boundary. Do not re-inject old
                # read bodies when a capability has reported invalid versions.
                from research_agent.tool_context import compact_completed
                compact_completed(restored["messages"], self.ctx.registry, self.ctx.state, fold_deferred=True)
        return restored

    def prepare(self) -> list[dict]:
        from research_agent.context import build_context
        messages = build_context(self.ctx.state, self.ctx.registry,
                                 getattr(self.ctx.llm, "model", ""))
        if self.ctx.registry:
            capabilities = self.ctx.registry.generate_capabilities()
            if capabilities:
                messages.insert(1, {"role": "system", "content": capabilities})
        from research_agent.tool_context import append_probe_notices
        append_probe_notices(messages, self.ctx.registry, self.ctx.state)
        return messages

    def select(self, messages: list[dict]) -> ModelResponse:
        self._check_cancelled()
        tools = self.ctx.registry.list_for_llm() if self.ctx.registry else []
        messages = self._budget(messages, tools)
        raw = _call_llm_with_retry(self.ctx, messages, tools)
        usage = raw.get("usage") or {}
        actual = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        if isinstance(actual, int) and self._last_budget is not None:
            delta = actual - self._last_budget.input_after
            self._token_overhead += max(0, delta)
            self.emit("context_calibration", {"actual_prompt_tokens": actual,
                       "estimated_prompt_tokens": self._last_budget.input_after,
                       "next_call_extra_tokens": self._token_overhead})
        return ModelResponse(raw.get("content", ""), tuple(
            ToolCall(tc["id"], tc["name"], tc["params"])
            for tc in raw.get("tool_calls", [])))

    def _budget(self, messages, tools):
        from research_agent.tool_context import compact_completed
        compact_completed(messages, self.ctx.registry, self.ctx.state)
        from research_agent.config import get_max_context_tokens, get_max_output_tokens, get_context_config
        from research_agent.context_budget import budget, estimate, ContextBudgetExceeded
        from dataclasses import asdict
        cfg = get_context_config()
        limit = int(cfg.get("window_tokens") or get_max_context_tokens(getattr(self.ctx.llm, "model", "")))
        reserve = get_max_output_tokens() or int(cfg.get("output_tokens") or min(4096, max(256, limit // 8)))
        margin = int(cfg.get("safety_tokens", min(2048, max(32, limit // 20))))
        history = messages
        constraints = getattr(self.ctx.state, "constraints", [])
        if constraints:
            if not isinstance(constraints, list) or any(not isinstance(item, str) for item in constraints):
                raise ContextBudgetExceeded("Constraints must be a list of strings")
            messages = list(messages) + [{"role": "system", "content": "[关键约束，不能被摘要替代]\n" + "\n".join(constraints)}]
        try:
            image_tokens = int(cfg.get("image_tokens", 4096))
            counter = self.ctx.token_counter
            if self._token_overhead:
                base_counter = counter
                counter = lambda items, schemas: estimate(items, schemas, base_counter, image_tokens) + self._token_overhead
            from research_agent.tool_context import fold_at_watermark
            saved = fold_at_watermark(messages, self.ctx.registry, self.ctx.state, tools,
                window=limit, reserve=reserve, margin=margin, counter=counter,
                image_tokens=image_tokens, config=cfg)
            if saved:
                if constraints:
                    history[:] = messages[:-1]
                self.emit("context_fold", {"trigger": "watermark", "saved_tokens": saved})
            fitted, report = budget(messages, tools, limit, reserve, margin=margin,
                                    counter=counter, image_tokens=image_tokens)
        except ContextBudgetExceeded as exc:
            self.emit("context_budget", {"status": "rejected", "reason": str(exc), "window": limit})
            raise
        self.ctx.output_limit = reserve
        self._last_budget = report
        self.emit("context_budget", dict(asdict(report), status="accepted"))
        return fitted

    def _check_cancelled(self):
        token = getattr(self.ctx.state, "_cancellation", None)
        if token is not None:
            token.check()

    def execute(self, call: ToolCall) -> ToolObservation:
        self._check_cancelled()
        from research_agent.tools.validate_params import validate_tool_params
        ctx = self.ctx
        ctx.state._current_tool_id = call.id
        self.emit("tool_start", {"id": call.id, "name": call.name, "input": call.params})
        try:
            from research_agent.execution import completed_call
            replay = completed_call(getattr(ctx.state, "_execution_id", ""), call.id, call.name, call.params)
            reason = (ctx.pre_tool_hook(ctx.state, call.name, call.params, ctx.emit)
                      if ctx.pre_tool_hook and not replay else None)
            if reason:
                observation = ToolObservation(False, {"error": reason}, blocked=True)
            else:
                error = validate_tool_params(call.name, call.params)
                if error:
                    observation = ToolObservation(False, {"error": error})
                elif ctx.registry is None:
                    observation = ToolObservation(False, {"error": "No tool registry"})
                else:
                    result = ctx.registry.dispatch(call.name, call.params, ctx.llm,
                                                   ctx.state, ctx.emit)
                    observation = ToolObservation(result.success, dict(result.data))
        except Exception as exc:
            observation = ToolObservation(False, {"error": str(exc)})
        self.emit("tool_end", {"id": call.id, "name": call.name,
                              "status": "success" if observation.success else "error",
                              "output": observation.data})
        from research_agent.context_budget import externalize, store_artifact
        if call.name != "context_result_read" and "context_result_read" in (self.ctx.registry.tools if hasattr(self.ctx.registry, "tools") else {}):
            from research_agent.config import get_context_config, get_max_context_tokens
            cfg = get_context_config()
            window = int(cfg.get("window_tokens") or get_max_context_tokens(getattr(ctx.llm, "model", "")))
            result_tokens = int(cfg.get("tool_result_tokens", min(2000, max(128, window // 8))))
            spec = self.ctx.registry.tools.get(call.name)
            projector = getattr(spec, "context_result", None)
            if projector is not None:
                key = store_artifact(observation.data, ctx.state)
                visible = externalize(projector(observation.data), ctx.state, max_tokens=result_tokens)
                visible = dict(visible, _tool_result_id=key)
                if visible.get("_truncated"):
                    visible["artifact_id"] = key
                    visible["characters"] = len(json.dumps(observation.data, ensure_ascii=False))
            else:
                visible = externalize(observation.data, ctx.state, max_tokens=result_tokens, always_store=True)
            delivery = getattr(spec, "context_delivery", None)
            if delivery:
                delivery(ctx.state, observation.data, visible)
            observation = ToolObservation(observation.success, visible, observation.blocked)
        return observation

    def after_tool(self, call: ToolCall, messages: list[dict]) -> None:
        if self.ctx.on_tool_success:
            self.ctx.on_tool_success(self.ctx.state, call.name, call.params,
                                     messages, self.ctx.emit)

    def answer(self, messages: list[dict], *, fallback: bool = False) -> str:
        self._check_cancelled()
        if getattr(self.ctx.state, "_replace_partial_reply", False):
            self.emit("reply_reset", {"text": "", "execution_id": getattr(self.ctx.state, "_execution_id", "")})
            self.ctx.state._replace_partial_reply = False
        if fallback:
            messages = _generate_msgs(messages, self.ctx.state)
        else:
            messages = list(messages) + [
                {"role": "system", "content": "基于以上工具调用结果和对话历史，用简洁的方式总结你完成了什么、结果如何。引用具体数据但不要重复完整内容。使用与用户相同的语言。"},
                {"role": "user", "content": self.ctx.user_input},
            ]
        return _default_stream(self.ctx, self._budget(messages, []))

    def emit(self, event_type: str, data: dict) -> None:
        _emit(self.ctx.emit, event_type, data)

    def set_round(self, number: int) -> None:
        self.ctx.state.round_count = number


class FunctionCallingRuntime(AgentRuntime):
    """Compatibility facade for the default kernel hosted by RuntimeHost."""

    id = "function_calling"

    def __init__(self, kernel: AgentKernel | None = None):
        from research_agent.kernel_factory import get_kernel
        self.kernel = get_kernel(kernel)

    def run(self, ctx: RuntimeContext) -> AgentState:
        ctx.state._kernel_id = self.kernel.id
        result = self.kernel.run(RuntimeHost(ctx), KernelRequest(ctx.user_input, ctx.max_rounds))
        ctx.state.final_response = result.response
        ctx.state.round_count = result.rounds
        ctx.state.termination_reason = result.reason.value
        return ctx.state


def _call_llm_with_retry(ctx: RuntimeContext, messages: list[dict], tools: list[dict]) -> dict:
    """Call LLM with retry/backoff for the tool-calling step."""
    from research_agent.model_retry import retry_model
    token = getattr(ctx.state, "_cancellation", None)
    return retry_model(lambda: _default_call_llm(ctx, messages, tools), ctx.emit, MAX_TOTAL_RETRIES,
                       wait=token.wait if token is not None else None)
