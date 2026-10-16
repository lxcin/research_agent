"""Asynchronous episodic-memory pipeline.

After a run, a task episode is distilled off the request thread: the small-model
`distill_episode` (when enabled) or a heuristic fallback, then persisted via
`plan.save_plan`. Failures never propagate to the request thread.

This is the successor to the Tier B conversation distillation for the
assistant-oriented agent: the automatic run-end memory write now produces
reusable task episodes (plan-as-memory) instead of semantic `MemoryUnit`s.
"""
import logging
import queue
import threading

from research_agent.plan import (
    Plan, distill_episode, episode_from_run, save_plan, build_episode_digest,
)

logger = logging.getLogger("research_agent.plan")

_q: queue.Queue = queue.Queue()
_worker: threading.Thread | None = None
_lock = threading.Lock()
_admission = threading.Condition()
_enabled = True
_active = 0


def pause(timeout=None):
    global _enabled
    with _admission:
        _enabled = False
        if not _admission.wait_for(lambda: _active == 0, timeout):
            raise TimeoutError("Episode worker still running; resources retained")


def resume():
    global _enabled
    with _admission:
        _enabled = True


class _Job:
    __slots__ = ("llm", "digest", "workspace_dir", "chat_id", "use_llm")

    def __init__(self, llm, digest, workspace_dir, chat_id, use_llm):
        self.llm = llm
        self.digest = digest
        self.workspace_dir = workspace_dir
        self.chat_id = chat_id
        self.use_llm = use_llm


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
    global _active
    while True:
        job = _q.get()
        admitted = False
        try:
            with _admission:
                if not _enabled:
                    continue
                _active += 1
                admitted = True
            filename, key, llm, retry_failed = job
            from research_agent import episode_jobs
            def invoke(payload):
                _process(_Job(llm, payload["digest"], payload["workspace_dir"],
                              payload["chat_id"], payload["use_llm"] and llm is not None))
            episode_jobs.process(filename, key, invoke, retry_failed=retry_failed)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"episode distillation failed: {e}")
        finally:
            if admitted:
                with _admission:
                    _active -= 1
                    _admission.notify_all()
            _q.task_done()


def _process(job: _Job):
    plan: Plan | None = None
    if job.use_llm:
        plan = distill_episode(job.llm, job.digest,
                               workspace_dir=job.workspace_dir, chat_id=job.chat_id)
    if plan is None:  # LLM off/failed → heuristic
        plan = episode_from_run(
            user_input=job.digest.get("user_input", ""),
            final_response=job.digest.get("final_response", ""),
            tool_path=job.digest.get("tool_path", []),
            workspace_dir=job.workspace_dir,
            chat_id=job.chat_id,
        )
    if job.digest.get("execution_id"):
        import hashlib
        plan.id = "run-" + hashlib.sha256(job.digest["execution_id"].encode()).hexdigest()
    if not save_plan(plan):
        raise RuntimeError("Episode persistence failed")


def submit(llm, digest: dict, workspace_dir: str = "", chat_id: str = "",
           use_llm: bool = True):
    """Enqueue an episode-distillation job. Returns immediately (non-blocking)."""
    import uuid
    from research_agent import episode_jobs
    digest = dict(digest)
    key = digest.setdefault("execution_id", uuid.uuid4().hex)
    filename, key = episode_jobs.enqueue(key, dict(digest=digest, workspace_dir=workspace_dir,
                                                  chat_id=chat_id, use_llm=use_llm))
    _ensure_worker()
    _q.put((filename, key, llm, False))


def submit_run(*, user_input: str = "", final_response: str = "",
               tool_path: list[str] | None = None, workspace_dir: str = "",
               chat_id: str = "", llm=None, use_llm: bool = True,
               execution_id: str | None = None):
    digest = build_episode_digest(user_input=user_input, final_response=final_response,
                                  tool_path=tool_path, workspace_dir=workspace_dir,
                                  chat_id=chat_id)
    if execution_id:
        digest["execution_id"] = execution_id
    submit(llm, digest, workspace_dir=workspace_dir, chat_id=chat_id, use_llm=use_llm)


def drain(timeout: float = 10.0):
    """Block until queued jobs are processed (used in tests)."""
    import time
    deadline = time.monotonic() + timeout
    with _q.all_tasks_done:
        while _q.unfinished_tasks:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Episode jobs have not finished")
            _q.all_tasks_done.wait(remaining)


def recover_pending(llm=None, *, retry_failed=False):
    from research_agent import episode_jobs
    jobs = episode_jobs.pending(retry_failed)
    _ensure_worker()
    for filename, key in jobs:
        _q.put((filename, key, llm, retry_failed))
    return len(jobs)
