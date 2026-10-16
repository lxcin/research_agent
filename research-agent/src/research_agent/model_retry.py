"""Host retry policy: transient failures only; published text is never replayed."""
import time
from contextvars import ContextVar

retry_wait = ContextVar("model_retry_wait", default=None)


class ModelRequestRejected(BaseException):
    pass


class ModelUnavailable(BaseException):
    """Transient transport retries exhausted; suspend, do not fail the loop."""


class PartialResponseError(BaseException):
    def __init__(self, partial, cause):
        self.partial = partial
        self.cause = cause
        super().__init__(f"Response interrupted after publication: {cause}")


def retryable(exc):
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None:
        return status in (408, 429, 500, 502, 503, 504)
    return isinstance(exc, (TimeoutError, ConnectionError)) or type(exc).__name__ in (
        "Timeout", "ReadTimeout", "ConnectTimeout", "APIConnectionError", "APITimeoutError", "RateLimitError",
        "ConnectionError", "ConnectError", "ReadError", "WriteError", "RemoteProtocolError", "NetworkError")


def retry_model(invoke, emit, max_retries=3, delays=(1, 2, 4), *, wait=None):
    for attempt in range(max_retries + 1):
        try:
            return invoke()
        except Exception as exc:
            if not retryable(exc):
                raise ModelRequestRejected(str(exc)) from exc
            if attempt == max_retries:
                raise ModelUnavailable(str(exc)) from exc
            if emit:
                emit("thinking", {"text": f"模型暂时故障，重试 {attempt + 1}/{max_retries}"})
            (wait or retry_wait.get() or time.sleep)(delays[min(attempt, len(delays) - 1)])


def stream_with_retry(invoke, emit, max_retries=3):
    published = []
    def forward(kind, data):
        if kind == "reply" and data.get("text"):
            published.append(data["text"])
        if emit:
            emit(kind, data)
    def call():
        try:
            return invoke(forward)
        except Exception as exc:
            if published:
                raise PartialResponseError("".join(published), exc) from exc
            raise
    return retry_model(call, emit, max_retries)
