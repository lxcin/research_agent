"""AgentRuntime — replaceable agent execution kernel (host-strategy split).

The host (research_agent.agent.run_agent) owns lifecycle concerns (workspace,
project state, tool registration, MCP, diagnostics wiring, event routing, and
post-run hooks). An AgentRuntime is the *strategy*: how the agent turns a user
request into a final response — the loop and tool-dispatch policy.

Replacing the kernel = implement AgentRuntime (same RuntimeContext contract) and
wire it in run_agent. The runtime is tool-agnostic: it never names a concrete
tool; approval policy (guardrail/HITL), result validation and post-write hooks
arrive via RuntimeContext callbacks supplied by the host.
"""
import json
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

from research_agent.models import AgentState
from research_agent.llm import LLMProvider
from research_agent.config import get_temperature, get_max_output_tokens

MAX_ROUNDS = int(os.environ.get("RESEARCH_AGENT_MAX_ROUNDS", "50"))
MAX_TOTAL_RETRIES = 5
LLM_RETRY_BACKOFF = [1, 2, 4]

EventCallback = Callable[[str, dict], None]


@dataclass
class RuntimeContext:
    """Everything a strategy needs to execute one agent request."""
    llm: LLMProvider
    user_input: str
    state: AgentState
    registry: object = None          # ToolRegistry (dispatch + capabilities)
    workspace_dir: str = ""
    chat_id: str = ""
    emit: EventCallback | None = None
    # Called before a tool is dispatched. Signature:
    #   pre_tool_hook(state, tool_name, params, emit) -> str | None
    # Return an approval/block reason string to reject the call (and the caller
    # feeds it back to the model), or None to allow. The kernel does NOT know any
    # specific tool name — dangerous-action policy is entirely host-injected.
    pre_tool_hook: Callable | None = None
    # Called after a successful file_write/file_edit (host hook for validation).
    on_tool_success: Callable | None = None
    max_rounds: int = MAX_ROUNDS
    # Injectable LLM-call primitives. The host provides wrappers that resolve the
    # patched names at call time (keeps `patch('research_agent.agent._call_llm
    # _with_tools')` working in tests). Defaults to the runtime-local functions.
    call_llm_with_tools: Callable | None = None
    stream_response: Callable | None = None


def _emit(event: EventCallback | None, event_type: str, data: dict):
    if event:
        event(event_type, data)


def _history_budget() -> int:
    """Mid-turn cap on non-system message tokens (config context.max_history_tokens)."""
    try:
        from research_agent.config import get_context_config
        return int(get_context_config().get("max_history_tokens", 24000))
    except Exception:
        return 24000


def _wall_budget() -> float:
    """Optional wall-clock cap per run (seconds); 0 = unlimited."""
    try:
        return float(os.environ.get("RESEARCH_AGENT_MAX_WALL_S", "0") or 0)
    except ValueError:
        return 0.0


def _default_call_llm(ctx: RuntimeContext, messages, tools):
    fn = ctx.call_llm_with_tools or _call_llm_with_tools
    return fn(ctx.llm, messages, tools, "auto")


def _default_stream(ctx: RuntimeContext, messages):
    fn = ctx.stream_response or _stream_response
    return fn(ctx.llm, messages, ctx.emit)


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
    return result


def _stream_response_once(llm: LLMProvider, messages: list[dict], emit: EventCallback):
    import litellm
    model = getattr(llm, "model", "openai/deepseek-chat")
    kwargs = getattr(llm, "_kwargs", {})
    api_key = getattr(llm, "api_key", None)
    litellm_kw = dict(model=model, messages=messages,
                      temperature=get_temperature(0.7),
                      api_key=api_key, stream=True, **kwargs)
    mt = get_max_output_tokens()
    if mt:
        litellm_kw["max_tokens"] = mt
    resp = litellm.completion(**litellm_kw)
    content = ""
    for chunk in resp:
        delta = chunk.choices[0].delta
        if delta.content:
            content += delta.content
            _emit(emit, "reply", {"text": delta.content})
    return content


def _stream_response(llm: LLMProvider, messages: list[dict], emit: EventCallback) -> str:
    """Stream LLM response token-by-token with retry."""
    last_error = None
    for attempt in range(MAX_TOTAL_RETRIES + 1):
        try:
            return _stream_response_once(llm, messages, emit)
        except Exception as e:
            last_error = e
            if attempt < MAX_TOTAL_RETRIES:
                delay = LLM_RETRY_BACKOFF[min(attempt, len(LLM_RETRY_BACKOFF) - 1)]
                _emit(emit, "thinking", {"text": f"LLM 重试 {attempt+1}/{MAX_TOTAL_RETRIES}: {str(e)[:80]}"})
                time.sleep(delay)
    raise last_error


def _generate_msgs(messages: list[dict], state) -> list[dict]:
    """Build a plain (no tools) message tail with tool results for final answer."""
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    results = []
    for i, tm in enumerate(tool_msgs):
        content = tm.get("content", "")[:6000]
        results.append({"role": "system", "content": f"[工具结果 {i+1}]\n{content}"})
    return [
        {"role": "system", "content": "=== 工具调用结果 ==="},
        *results,
        {"role": "system", "content": "=== 工具结果结束，回答用户问题 ==="},
        {"role": "user", "content": state.user_input},
    ]


class AgentRuntime(ABC):
    """Strategy contract for turning a request into a final answer."""

    id: str = "base"

    @abstractmethod
    def run(self, ctx: RuntimeContext) -> AgentState:
        """Execute one agent turn. Fills state.final_response; returns state."""


class FunctionCallingRuntime(AgentRuntime):
    """while-loop + OpenAI function-calling kernel (the default strategy)."""

    id = "function_calling"

    def run(self, ctx: RuntimeContext) -> AgentState:
        state = ctx.state
        emit = ctx.emit
        llm = ctx.llm
        registry = ctx.registry

        from research_agent.context import build_context, trim_history
        messages = build_context(state, registry, getattr(llm, "model", ""))
        capabilities = registry.generate_capabilities() if registry else ""
        if capabilities:
            messages.insert(1, {"role": "system", "content": capabilities})

        tools_list = registry.list_for_llm() if registry else []
        total_retries = 0
        run_started = time.time()

        for round_num in range(1, ctx.max_rounds + 1):
            state.round_count = round_num
            # Cost controls (mid-turn): trim old context to a token budget and
            # enforce a wall-clock cap, so a long run can't balloon unchecked.
            messages = trim_history(messages, _history_budget())
            wall = _wall_budget()
            if wall and time.time() - run_started > wall:
                _emit(emit, "thinking", {"text": f"达到运行时长上限 {wall:g}s，收口作答"})
                state.final_response = _default_stream(ctx, _generate_msgs(messages, state))
                break
            if total_retries >= MAX_TOTAL_RETRIES:
                _emit(emit, "thinking", {"text": "重试次数已达上限"})
                state.final_response = _default_stream(ctx, _generate_msgs(messages, state))
                break

            try:
                response = _call_llm_with_retry(ctx, messages, tools_list)
            except Exception as e:
                total_retries += 1
                _emit(emit, "thinking", {"text": f"模型调用失败 (尝试 {total_retries}/{MAX_TOTAL_RETRIES}): {str(e)[:100]}"})
                messages.append({"role": "system", "content": f"模型调用失败: {e}。请调整参数重试。"})
                if total_retries >= MAX_TOTAL_RETRIES:
                    state.final_response = "抱歉，模型多次调用失败。"
                continue

            tool_calls = response.get("tool_calls", [])
            if not tool_calls:
                clean_msgs = [m for m in messages if m["role"] in ("system", "user", "tool", "assistant")]
                clean_msgs.append({"role": "system",
                    "content": "基于以上工具调用结果和对话历史，用简洁的方式总结你完成了什么、结果如何。引用具体数据但不要重复完整内容。使用与用户相同的语言。"})
                clean_msgs.append({"role": "user", "content": state.user_input})
                state.final_response = _default_stream(ctx, clean_msgs)
                break

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
                tc_id = tc["id"]
                tc_name = tc["name"]
                tc_input = tc["params"]
                state._current_tool_id = tc_id

                _emit(emit, "tool_start", {"id": tc_id, "name": tc_name, "input": tc_input})

                # Pre-dispatch approval policy (host-injected). Kernel is tool-agnostic.
                if ctx.pre_tool_hook:
                    block_reason = ctx.pre_tool_hook(state, tc_name, tc_input, emit)
                    if block_reason:
                        _emit(emit, "tool_end", {"id": tc_id, "name": tc_name, "status": "error", "output": {"error": block_reason}})
                        messages.append({"role": "tool", "tool_call_id": tc_id,
                                         "content": json.dumps({"error": block_reason}, ensure_ascii=False)})
                        continue

                from research_agent.tools.validate_params import validate_tool_params
                param_err = validate_tool_params(tc_name, tc_input)
                if param_err:
                    total_retries += 1; round_retry += 1
                    _emit(emit, "tool_end", {"id": tc_id, "name": tc_name, "status": "error", "output": {"error": param_err}})
                    messages.append({"role": "tool", "tool_call_id": tc_id,
                                     "content": json.dumps({"error": param_err}, ensure_ascii=False)})
                    continue

                result = registry.dispatch(tc_name, tc_input, llm, state, emit)

                if not result.success:
                    total_retries += 1; round_retry += 1
                    # Prefer explicit error; else derive from stderr/returncode payload.
                    err_detail = result.data.get("error", "") or ""
                    if result.data.get("stderr"):
                        err_detail = (err_detail + "\n" if err_detail else "") + f"stderr: {result.data['stderr'][:500]}"
                    if result.data.get("stdout"):
                        err_detail = (err_detail + "\n" if err_detail else "") + f"stdout: {result.data['stdout'][:300]}"
                    if not err_detail and result.data.get("returncode") is not None:
                        err_detail = f"Command failed with exit code {result.data.get('returncode')}"
                    hint = f"工具'{tc_name}'失败: {err_detail}" if err_detail else f"工具'{tc_name}'失败"
                    if round_retry >= 2:
                        hint += " 请换其他方式回答。"
                    _emit(emit, "tool_end", {"id": tc_id, "name": tc_name, "status": "error", "output": {"error": result.data.get("error", err_detail)[:100]}})
                    messages.append({"role": "tool", "tool_call_id": tc_id,
                                     "content": json.dumps({"error": hint, "stdout": result.data.get("stdout", "")[:300]}, ensure_ascii=False)})
                    continue

                _emit(emit, "tool_end", {"id": tc_id, "name": tc_name, "status": "success", "output": result.data})
                messages.append({"role": "tool", "tool_call_id": tc_id,
                                 "content": json.dumps(result.data, ensure_ascii=False)})

                if ctx.on_tool_success:
                    ctx.on_tool_success(state, tc_name, tc_input, messages, emit)

        if not state.final_response:
            state.final_response = _default_stream(ctx, _generate_msgs(messages, state))
        return state


def _call_llm_with_retry(ctx: RuntimeContext, messages: list[dict], tools: list[dict]) -> dict:
    """Call LLM with retry/backoff for the tool-calling step."""
    last_error = None
    for attempt in range(MAX_TOTAL_RETRIES + 1):
        try:
            return _default_call_llm(ctx, messages, tools)
        except Exception as e:
            last_error = e
            if attempt < MAX_TOTAL_RETRIES:
                delay = LLM_RETRY_BACKOFF[min(attempt, len(LLM_RETRY_BACKOFF) - 1)]
                _emit(ctx.emit, "thinking", {"text": f"LLM 重试 {attempt+1}/{MAX_TOTAL_RETRIES}: {str(e)[:80]}"})
                time.sleep(delay)
    raise last_error

