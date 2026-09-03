"""Asynchronous distillation pipeline for Tier B memory write path.

Turn snapshots are submitted after each saved turn; a background worker runs
EXTRACT → VERIFY → write through MemoryManager. Failures never propagate to the
request thread; the worker logs and moves on.
"""
import logging
import queue
import threading

from research_agent.memory.models import MemoryScope
from research_agent.memory.extractor import distill
from research_agent.config import get_memory_config

logger = logging.getLogger("research_agent.memory")

_q: queue.Queue = queue.Queue()
_worker: threading.Thread | None = None
_lock = threading.Lock()


class _Job:
    __slots__ = ("llm", "conversation", "notes", "scope", "source")

    def __init__(self, llm, conversation, notes, scope, source):
        self.llm = llm
        self.conversation = conversation
        self.notes = notes
        self.scope = scope
        self.source = source


def _ensure_worker():
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    with _lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(target=_run_loop, daemon=True)
        _worker.start()


def _run_loop():
    while True:
        job = _q.get()
        try:
            _process(job)
        except Exception as e:
            logger.warning(f"memory distillation failed: {e}")
        finally:
            _q.task_done()


def _process(job: _Job):
    """EXTRACT (+ optional note context) → VERIFY → write."""
    from research_agent.memory import get_manager
    conv = job.conversation
    if not conv.strip():
        return
    # Notes are appended as conversational context (they are distilled text).
    if job.notes:
        conv = conv + "\n\n[项目笔记]\n" + job.notes
    units = distill(job.llm, conv, scope=job.scope, source=job.source)
    if not units:
        return
    mgr = get_manager()
    for u in units:
        try:
            mgr.write(u)
        except Exception as e:
            logger.warning(f"memory write failed: {e}")


def submit(conversation: str, llm, notes: str = "",
           scope: MemoryScope = MemoryScope.USER, source: dict | None = None):
    """Enqueue a distillation job. Returns immediately (non-blocking)."""
    if not get_memory_config().get("enabled", True):
        return
    if not conversation or not conversation.strip():
        return
    _q.put(_Job(llm, conversation, notes, scope, source))
    _ensure_worker()


def drain(timeout: float = 10.0):
    """Block until queued jobs are processed (used in tests)."""
    _q.join()
