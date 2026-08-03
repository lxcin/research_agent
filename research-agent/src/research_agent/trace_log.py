"""Structured logging with trace IDs and request context."""
import logging
import uuid
import time
import threading

_logger = logging.getLogger("research_agent")
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(trace_id)s] %(levelname)s %(message)s"))
    _logger.addHandler(h)

_trace_local = threading.local()


def set_trace_id(trace_id: str = ""):
    _trace_local.trace_id = trace_id or str(uuid.uuid4())[:8]


def get_trace_id() -> str:
    return getattr(_trace_local, "trace_id", "--------")


class TraceAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return f"[{get_trace_id()}] {msg}", kwargs


logger = TraceAdapter(_logger, {})


def trace(module: str):
    """Decorator: wraps function with trace start/end logging."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            t0 = time.time()
            logger.info(f"{module}.{func.__name__} START")
            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - t0
                logger.info(f"{module}.{func.__name__} OK ({elapsed:.2f}s)")
                return result
            except Exception as e:
                elapsed = time.time() - t0
                logger.error(f"{module}.{func.__name__} FAIL ({elapsed:.2f}s): {e}")
                raise
        return wrapper
    return decorator
