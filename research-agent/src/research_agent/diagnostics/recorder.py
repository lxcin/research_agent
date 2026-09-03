"""EventRecorder — unified diagnostic event stream.

Every emit() the agent produces is routed through an EventRecorder so it lands
as a JSONL line in data_dir/logs/{trace_id}.jsonl (real-time, append-only).
The same event is then forwarded to the original SSE callback unchanged, so
adding diagnostics never alters UI behaviour.

Large payloads are truncated to keep log files bounded.
"""
import json
import os
import threading
from datetime import datetime, timezone

from research_agent.config import get_data_dir

MAX_STR = 1000
MAX_LIST = 100


def _clip(value, depth=0):
    """Recursively clip oversized / non-JSON-safe payloads."""
    if depth > 4:
        return "[...]"
    if isinstance(value, str):
        return value[:MAX_STR] if len(value) > MAX_STR else value
    if isinstance(value, list):
        return [_clip(v, depth + 1) for v in value[:MAX_LIST]]
    if isinstance(value, dict):
        return {k: _clip(v, depth + 1) for k, v in list(value.items())[:MAX_LIST]}
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    try:
        return str(value)[:MAX_STR]
    except Exception:
        return repr(value)[:MAX_STR]


class EventRecorder:
    """Appends every agent event to a per-trace JSONL file."""

    def __init__(self, trace_id: str = "", workspace_dir: str = "",
                 chat_id: str = "", data_dir=None, enabled: bool = True):
        self.trace_id = trace_id or "run"
        self.workspace_dir = workspace_dir
        self.chat_id = chat_id
        self.enabled = enabled
        self._lock = threading.Lock()
        self._path = None
        self._count = 0
        if enabled:
            base = data_dir or get_data_dir()
            logs_dir = os.path.join(str(base), "logs")
            os.makedirs(logs_dir, exist_ok=True)
            safe_trace = "".join(c for c in self.trace_id if c.isalnum() or c in "-_")[:64]
            self._path = os.path.join(logs_dir, f"{safe_trace}.jsonl")

    @property
    def path(self):
        return self._path

    def record(self, event_type: str, data: dict):
        """Write one event line (or a synthetic fault line)."""
        if not self.enabled or self._path is None:
            return
        line = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "trace": self.trace_id,
            "workspace": self.workspace_dir,
            "chat": self.chat_id,
            "event": event_type,
            "data": _clip(data),
        }
        with self._lock:
            try:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
                self._count += 1
            except OSError:
                pass

    def wrap(self, on_event):
        """Return an emit-compatible function that records then forwards."""
        def _emit(event_type: str, data: dict):
            self.record(event_type, data)
            if on_event:
                try:
                    on_event(event_type, data)
                except Exception:
                    pass
        return _emit
