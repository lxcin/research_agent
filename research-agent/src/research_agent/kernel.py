"""Thinking strategies. No workspace, provider, registry or application imports."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
import json
from typing import Protocol


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    params: dict


@dataclass(frozen=True)
class ModelResponse:
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class ToolObservation:
    success: bool
    data: dict
    blocked: bool = False


class TerminationReason(str, Enum):
    COMPLETED = "completed"
    ROUND_LIMIT = "round_limit"
    FAILURE_LIMIT = "failure_limit"


@dataclass(frozen=True)
class KernelRequest:
    user_input: str
    max_rounds: int = 50

    def __post_init__(self):
        if self.max_rounds < 1:
            raise ValueError("max_rounds must be positive")


@dataclass(frozen=True)
class KernelResult:
    response: str
    rounds: int = 0
    reason: TerminationReason = TerminationReason.COMPLETED


class KernelHost(Protocol):
    def prepare(self) -> list[dict]: ...
    def select(self, messages: list[dict]) -> ModelResponse: ...
    def execute(self, call: ToolCall) -> ToolObservation: ...
    def after_tool(self, call: ToolCall, messages: list[dict]) -> None: ...
    def answer(self, messages: list[dict], *, fallback: bool = False) -> str: ...
    def emit(self, event_type: str, data: dict) -> None: ...
    def set_round(self, number: int) -> None: ...


class AgentKernel(ABC):
    id = "base"

    @abstractmethod
    def run(self, host: KernelHost, request: KernelRequest) -> KernelResult:
        """Use host interactions and return a result; do not mutate application state."""


class _ProgressGuard:
    def __init__(self, loop_limit: int = 3, error_limit: int = 3):
        self.loop_limit, self.error_limit = loop_limit, error_limit
        self._last_sig = self._last_err = None
        self._repeat = self._err_streak = 0

    def note_call(self, name: str, params: dict) -> str | None:
        sig = name + "|" + json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
        self._repeat = self._repeat + 1 if sig == self._last_sig else 1
        self._last_sig = sig
        if self._repeat >= self.loop_limit:
            self._repeat, self._last_sig = 0, None
            return f"[loop_guard] 同一工具以相同参数重复调用（{name}）。请换参数/换工具/换思路，或直接作答；不要原样重试。"
        return None

    def note_result(self, name: str, success: bool) -> str | None:
        if success:
            self._err_streak, self._last_err = 0, None
            return None
        self._err_streak = self._err_streak + 1 if name == self._last_err else 1
        self._last_err = name
        if self._err_streak >= self.error_limit:
            self._err_streak = 0
            return f"[loop_guard] 工具 {name} 连续失败。请换参数/换工具/换思路，或直接作答；不要原样重试。"
        return None


def _attach_hint(payload: dict, hint: str | None) -> dict:
    if hint:
        payload["_loop_hint"] = hint
    return payload


class FunctionCallingKernel(AgentKernel):
    id = "function_calling"

    def run(self, host: KernelHost, request: KernelRequest) -> KernelResult:
        persist = getattr(host, "checkpoint", lambda payload=None: None)
        restored = persist()
        messages = restored["messages"] if restored else host.prepare()
        failures = restored.get("failures", 0) if restored else 0
        rounds = restored.get("rounds", 0) if restored else 0
        pending = [ToolCall(**call) for call in restored.get("pending", [])] if restored else []
        if restored and "final" in restored:
            return KernelResult(restored["final"], rounds, TerminationReason(restored["reason"]))
        guard = _ProgressGuard()
        if restored:
            for key in ("_last_sig", "_last_err", "_repeat", "_err_streak"):
                if key in restored.get("guard", {}):
                    setattr(guard, key, restored["guard"][key])
        phase = restored.get("phase", "tools" if pending else "next") if restored else "next"
        if phase not in {"next", "select", "tools", "answer"}:
            raise ValueError("Unknown execution checkpoint phase")
        fallback = restored.get("fallback", False) if restored else False
        max_rounds = min(request.max_rounds, restored.get("max_rounds", request.max_rounds)) if restored else request.max_rounds
        def save(**extra):
            from dataclasses import asdict
            persist(dict(messages=messages, failures=failures, rounds=rounds,
                         pending=[asdict(call) for call in pending], phase=phase,
                         fallback=fallback, max_rounds=max_rounds,
                         guard={key: getattr(guard, key) for key in
                                ("_last_sig", "_last_err", "_repeat", "_err_streak")}, **extra))
        while True:
            if phase == "next":
                if failures >= 5 or rounds >= max_rounds:
                    phase, fallback = "answer", True
                else:
                    rounds += 1
                    phase = "select"
                save()  # Includes the first model request and its round budget.
            if phase == "select":
                host.set_round(rounds)
                try:
                    response = host.select(messages)
                except Exception as exc:
                    failures += 1
                    host.emit("thinking", {"text": f"模型调用失败: {str(exc)[:100]}"})
                    phase = "next"
                    save()
                    continue
                if not response.tool_calls:
                    phase, fallback = "answer", False
                else:
                    phase = "tools"
                    pending = list(response.tool_calls)
                    messages.append({"role": "assistant", "content": None, "tool_calls": [
                        {"id": call.id, "type": "function", "function": {
                            "name": call.name, "arguments": json.dumps(call.params)}} for call in pending]})
                save()  # durable intent before any side effect
            while pending:
                call = pending[0]
                observation = host.execute(call)
                repeated = guard.note_call(call.name, call.params)
                error_hint = guard.note_result(call.name, observation.success)
                if not observation.success and not observation.blocked:
                    failures += 1
                payload = _attach_hint(dict(observation.data), repeated or error_hint)
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(payload, ensure_ascii=False)})
                if observation.success:
                    host.after_tool(call, messages)
                pending.pop(0)
                save()
            if phase == "tools":
                phase = "next"
                save()
                continue
            if phase == "answer":
                reason = (TerminationReason.FAILURE_LIMIT if failures >= 5 else TerminationReason.ROUND_LIMIT) if fallback else TerminationReason.COMPLETED
                final = host.answer(messages, fallback=fallback)
                save(final=final, reason=reason.value)
                return KernelResult(final, rounds, reason)
