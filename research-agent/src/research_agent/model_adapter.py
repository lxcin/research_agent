"""Provider transport adapter. Retry and lifecycle remain in the runtime host."""
import json
import time
from dataclasses import dataclass
from typing import Callable, Protocol
from research_agent.llm import LLMProvider
from research_agent.config import get_temperature, get_max_output_tokens

EventCallback = Callable[[str, dict], None]

@dataclass(frozen=True)
class ModelSettings:
    tool_temperature: float
    answer_temperature: float
    max_output_tokens: int | None

    @classmethod
    def from_config(cls):
        return cls(get_temperature(0.3), get_temperature(0.7), get_max_output_tokens())

class ModelAdapter(Protocol):
    def select(self, messages: list[dict], tools: list[dict], tool_choice: str = "auto") -> dict: ...
    def answer(self, messages: list[dict], emit: EventCallback | None) -> str: ...


def _emit(event, event_type, data):
    if event:
        event(event_type, data)

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


def call_with_tools(llm: LLMProvider, messages: list[dict],
                         tools: list[dict], tool_choice: str = "auto", *, settings: ModelSettings | None = None) -> dict:
    """Call LLM with function calling support. Returns {content, tool_calls}."""
    import litellm
    settings = settings or ModelSettings.from_config()
    model = getattr(llm, "model", "openai/deepseek-chat")
    kwargs = dict(getattr(llm, "_kwargs", {}))
    kwargs.setdefault("timeout", 60)
    kwargs.setdefault("num_retries", 0)
    api_key = getattr(llm, "api_key", None)

    from research_agent.context_budget import output_cap
    cap = output_cap.get() or settings.max_output_tokens
    if cap:
        kwargs["max_tokens"] = cap
    resp = litellm.completion(
        model=model, messages=messages,
        tools=tools if tools else None,
        tool_choice=tool_choice if tools else None,
        temperature=settings.tool_temperature,
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


def stream_once(llm: LLMProvider, messages: list[dict], emit: EventCallback | None,
                *, settings: ModelSettings | None = None):
    import litellm
    settings = settings or ModelSettings.from_config()
    model = getattr(llm, "model", "openai/deepseek-chat")
    kwargs = dict(getattr(llm, "_kwargs", {}))
    kwargs.setdefault("timeout", 60)
    kwargs.setdefault("num_retries", 0)
    api_key = getattr(llm, "api_key", None)
    litellm_kw = dict(model=model, messages=messages,
                      temperature=settings.answer_temperature,
                      api_key=api_key, stream=True, **kwargs)
    from research_agent.context_budget import output_cap
    mt = output_cap.get() or settings.max_output_tokens
    if mt: litellm_kw["max_tokens"] = mt
    t0 = time.monotonic()
    try:
        resp = litellm.completion(**litellm_kw, stream_options={"include_usage": True})
    except Exception as exc:
        message = str(exc).lower()
        if getattr(exc, "status_code", None) != 400 or "stream_options" not in message or not any(
                word in message for word in ("unsupported", "unknown", "unrecognized", "not supported")):
            raise
        resp = litellm.completion(**litellm_kw)
    content = ""
    usage = None
    finished = False
    try:
        for chunk in resp:
            u = getattr(chunk, "usage", None)
            if u is not None:
                usage = u
            if not chunk.choices:
                continue
            if getattr(chunk.choices[0], "finish_reason", None) is not None:
                finished = True
            delta = chunk.choices[0].delta
            if delta.content:
                content += delta.content
                _emit(emit, "reply", {"text": delta.content})
        if not finished:
            raise ConnectionError("Model stream ended before its completion boundary")
    finally:
        close = getattr(resp, "close", None)
        if callable(close):
            close()
    # Emit usage through the existing event channel (observed, never in the loop).
    if emit is not None and usage is not None:
        payload = {"model": model,
                   "latency_ms": round((time.monotonic() - t0) * 1000, 1),
                   "purpose": "answer"}
        payload.update(_usage_dict(usage))
        _emit(emit, "llm_usage", payload)
    return content


class ProviderModelAdapter:
    """Bind provider and settings once; request messages are never stored here."""
    def __init__(self, provider: LLMProvider, settings: ModelSettings | None = None):
        self.provider = provider
        self.settings = settings or ModelSettings.from_config()

    def select(self, messages, tools, tool_choice="auto"):
        return call_with_tools(self.provider, messages, tools, tool_choice, settings=self.settings)

    def answer(self, messages, emit):
        return stream_once(self.provider, messages, emit, settings=self.settings)
