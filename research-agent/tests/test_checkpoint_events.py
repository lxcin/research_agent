"""Durable completion messages must never seal a delayed human edit as E."""
from contextlib import ExitStack, suppress
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
from types import SimpleNamespace

import pytest

from research_agent import checkpoint as cp, checkpoint_events as events
from research_agent.background_tasks import BackgroundTasks, _db, _database
from research_agent.execution import Cancelled, RecoveryRequired, exclusive_request
from research_agent.review_baseline import baseline


def git(repo, *args):
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "core.autocrlf", "false")
    (repo / "file.txt").write_bytes(b"one\ntwo\nthree\nfour\nfive\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "initial")
    return str(repo)


def begin(repo, lease, execution="event-run"):
    state = SimpleNamespace(_execution_id=execution, active_chat_id="event-chat", _request_lease=lease)
    ref = cp.snapshot_for_run(state, repo)
    return state, ref


def start(manager, repo, lease, ref, release, *, name="file.txt", terminal="done", cleanup_failure=False):
    entered = Event()
    resources = ExitStack()
    resources.callback(lease.retain())
    if cleanup_failure:
        resources.callback(lambda: (_ for _ in ()).throw(OSError("cleanup failed")))
    def invoke(token):
        entered.set()
        while not release.wait(0.01):
            token.check()
        (Path(repo) / name).write_bytes(b"one\ntwo\nagent\nfour\nfive\n")
        if terminal == "unknown":
            raise RecoveryRequired("unknown command side effects")
        if terminal == "cancelled":
            raise Cancelled()
        return {"success": terminal == "done", "returncode": 0 if terminal == "done" else 1}
    descriptor = manager.start(chat="event-chat", execution="event-run", workspace=repo, command="write",
                               backend="local", invoke=invoke, leases=resources, request_lease=lease, checkpoint=ref)
    assert entered.wait(3)
    return descriptor


def test_background_after_reply_seals_on_last_writer_before_unlock(repo):
    manager, release = BackgroundTasks(), Event()
    with exclusive_request(repo, "event-chat") as lease:
        state, ref = begin(repo, lease)
        descriptor = start(manager, repo, lease, ref, release)
        events.foreground_finished(lease, safe=True)
    assert not baseline(repo, "checkpoint:" + baseline(repo, "active-turn")["sha"])
    with pytest.raises(RuntimeError):
        with exclusive_request(repo, "other"):
            pass
    # The standalone snapshot entry must obey the same foreground/background
    # workspace lease, even without a host-supplied state lease.
    with pytest.raises(RuntimeError):
        cp.snapshot_for_run(SimpleNamespace(), repo)
    release.set()
    manager.drain()
    sha = baseline(repo, "active-turn")["sha"]
    assert baseline(repo, "checkpoint:" + sha)["files"]["file.txt"] == hashlib.sha256((Path(repo) / "file.txt").read_bytes()).hexdigest()
    assert events.status(repo)[0]["status"] == "sealed"
    with _db(_database()) as db:
        kinds = {row[0] for row in db.execute("SELECT kind FROM snapshot_events")}
        assert kinds == {"foreground_finished", "task_finished", "snapshot_ready"}
        assert db.execute("SELECT count(*) FROM snapshot_events WHERE status<>'done' OR payload<>'{}'").fetchone()[0] == 0
    with exclusive_request(repo, "next") as next_lease:
        begin(repo, next_lease, "next-execution")
        events.foreground_finished(next_lease, safe=True)
    assert len(events.status(repo)) == 1


def test_task_finishing_before_foreground_does_not_seal_early(repo):
    manager, release = BackgroundTasks(), Event()
    with exclusive_request(repo, "event-chat") as lease:
        state, ref = begin(repo, lease)
        start(manager, repo, lease, ref, release)
        release.set()
        manager.drain()
        sha = baseline(repo, "active-turn")["sha"]
        assert not baseline(repo, "checkpoint:" + sha)
        (Path(repo) / "foreground.txt").write_bytes(b"foreground still writing")
        events.foreground_finished(lease, safe=True)
    assert "foreground.txt" in baseline(repo, "checkpoint:" + sha)["files"]


def test_multiple_tasks_wait_for_all_completions(repo):
    manager, first, second = BackgroundTasks(), Event(), Event()
    with exclusive_request(repo, "event-chat") as lease:
        state, ref = begin(repo, lease)
        start(manager, repo, lease, ref, first, name="first.txt")
        start(manager, repo, lease, ref, second, name="second.txt")
        events.foreground_finished(lease, safe=True)
    try:
        first.set()
        # Joining only the first task establishes its completion before checking E.
        with manager._lock:
            threads = [job.thread for job in manager._jobs.values()]
        threads[0].join(timeout=5)
        sha = baseline(repo, "active-turn")["sha"]
        assert not baseline(repo, "checkpoint:" + sha)
    finally:
        second.set()
        manager.drain()
    assert {"first.txt", "second.txt"}.issubset(baseline(repo, "checkpoint:" + sha)["files"])


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
def test_confirmed_failure_and_cancellation_still_allow_undo(repo, terminal):
    manager, release = BackgroundTasks(), Event()
    with exclusive_request(repo, "event-chat") as lease:
        state, ref = begin(repo, lease)
        start(manager, repo, lease, ref, release, terminal=terminal)
        events.foreground_finished(lease, safe=True)
    release.set()
    manager.drain()
    assert cp.review_checkpoint(repo, ref)["success"]


@pytest.mark.parametrize("failure", ["task-unknown", "cleanup", "foreground-unknown"])
def test_unknown_outcomes_never_generate_ready_evidence(repo, failure):
    manager, release = BackgroundTasks(), Event()
    with exclusive_request(repo, "event-chat") as lease:
        state, ref = begin(repo, lease)
        start(manager, repo, lease, ref, release, terminal="unknown" if failure == "task-unknown" else "done",
              cleanup_failure=failure == "cleanup")
        events.foreground_finished(lease, safe=failure != "foreground-unknown")
    release.set()
    with suppress(RuntimeError):
        manager.drain()
    sha = baseline(repo, "active-turn")["sha"]
    assert not baseline(repo, "checkpoint:" + sha)
    assert events.status(repo)[0]["status"] == "recovery_required"
    with _db(_database()) as db:
        assert db.execute("SELECT 1 FROM snapshot_events WHERE kind='snapshot_ready'").fetchone() is None
    if failure != "foreground-unknown":
        with pytest.raises((RuntimeError, RecoveryRequired)):
            cp.snapshot_for_run(SimpleNamespace(), repo)


@pytest.mark.parametrize("task_status", ["running", "cancelling", "recovery_required"])
def test_pending_task_blocks_new_baseline_even_without_end_evidence(repo, task_status):
    # Admission cannot equate a missing E with either a live writer or safety.
    # The independently persisted task state is authoritative.
    with _db(_database(), initialize=True) as db:
        db.execute("INSERT INTO tasks(id,chat,execution,workspace,command,backend,started,status) VALUES(?,?,?,?,?,?,?,?)",
                   ("pending", "chat", "execution", events._canonical(repo), "write", "local", 0, task_status))
    with pytest.raises(RecoveryRequired, match="drained or reconciled"):
        cp.snapshot_for_run(SimpleNamespace(), repo)
    assert not cp.list_checkpoints(repo)


def test_delayed_message_preserves_human_edit_and_is_idempotent(repo, monkeypatch):
    manager, release = BackgroundTasks(), Event()
    actual_consume = events.consume_workspace
    with exclusive_request(repo, "event-chat") as lease:
        state, ref = begin(repo, lease)
        start(manager, repo, lease, ref, release)
        events.foreground_finished(lease, safe=True)
    with monkeypatch.context() as patch:
        patch.setattr(events, "consume_workspace", lambda *a, **k: None)
        release.set()
        manager.drain()
    assert events.status(repo)[0]["status"] == "ready"
    path = Path(repo) / "file.txt"
    path.write_bytes(path.read_bytes().replace(b"five", b"human-after"))
    actual_consume(repo)
    sha = baseline(repo, "active-turn")["sha"]
    evidence = baseline(repo, "checkpoint:" + sha)
    assert evidence["files"]["file.txt"] != hashlib.sha256(path.read_bytes()).hexdigest()
    actual_consume(repo)
    assert baseline(repo, "checkpoint:" + sha) == evidence
    from research_agent.rollback import review_undo, apply_undo
    review = review_undo(repo, ref)
    assert review["success"], review
    assert apply_undo(repo, review_token=review["review_token"])["success"]
    assert path.read_bytes() == b"one\ntwo\nthree\nfour\nhuman-after\n"


def test_publish_before_ack_replays_without_refreshing_baseline(repo, monkeypatch):
    manager, release = BackgroundTasks(), Event()
    with exclusive_request(repo, "event-chat") as lease:
        state, ref = begin(repo, lease)
        start(manager, repo, lease, ref, release)
        events.foreground_finished(lease, safe=True)
    with monkeypatch.context() as patch:
        patch.setattr(events, "consume_workspace", lambda *a, **k: None)
        release.set()
        manager.drain()
    with _db(_database()) as db:
        import json
        evidence = json.loads(db.execute("SELECT payload FROM snapshot_events WHERE kind='snapshot_ready'").fetchone()[0])
    assert cp.publish_checkpoint_end(repo, ref, evidence)["success"]
    (Path(repo) / "file.txt").write_bytes(b"later human edit")
    events.consume_workspace(repo)
    assert events.status(repo)[0]["status"] == "sealed"
    assert baseline(repo, "checkpoint:" + baseline(repo, "active-turn")["sha"]) == evidence


@pytest.mark.parametrize("point", ["before-ready", "after-ready"])
def test_process_exit_at_queue_boundary_never_approves_new_current_files(repo, point):
    script = '''
import os, sys
from pathlib import Path
from types import SimpleNamespace
from research_agent import checkpoint as cp, checkpoint_events as events
from research_agent.execution import exclusive_request
with exclusive_request(sys.argv[1], 'crash') as lease:
    state = SimpleNamespace(_request_lease=lease, _execution_id='crash', active_chat_id='crash')
    cp.snapshot_for_run(state, sys.argv[1])
    (Path(sys.argv[1]) / 'file.txt').write_bytes(b'agent end')
    events.foreground_finished(lease, safe=True)
    if sys.argv[2] == 'before-ready':
        cp.capture_checkpoint_end = lambda *a, **k: os._exit(17)
    else:
        events.consume_workspace = lambda *a, **k: os._exit(17)
'''
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(cp.__file__).resolve().parents[1]) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, "-c", script, repo, point], env=env, capture_output=True, timeout=30)
    assert result.returncode == 17, result.stderr
    (Path(repo) / "file.txt").write_bytes(b"human after process exit")
    events.consume_workspace(repo)
    sha = baseline(repo, "active-turn")["sha"]
    evidence = baseline(repo, "checkpoint:" + sha)
    if point == "before-ready":
        assert not evidence
        assert events.status(repo)[0]["status"] == "undo_unavailable"
    else:
        assert evidence["files"]["file.txt"] == hashlib.sha256(b"agent end").hexdigest()
    assert (Path(repo) / "file.txt").read_bytes() == b"human after process exit"
    # A dead capture worker is not a live file writer. Save today's workspace,
    # rather than inventing old E or requiring approval to continue.
    with exclusive_request(repo, "next") as lease:
        begin(repo, lease, "after-crash")
        events.foreground_finished(lease, safe=True)
    assert len(events.status(repo)) == 1
    assert not baseline(repo, "checkpoint:" + sha)


def test_corrupt_ready_payload_cannot_approve_different_content(repo, monkeypatch):
    import json
    manager, release = BackgroundTasks(), Event()
    with exclusive_request(repo, "event-chat") as lease:
        state, ref = begin(repo, lease)
        start(manager, repo, lease, ref, release)
        events.foreground_finished(lease, safe=True)
    with monkeypatch.context() as patch:
        patch.setattr(events, "consume_workspace", lambda *a, **k: None)
        release.set()
        manager.drain()
    with _db(_database()) as db:
        evidence = json.loads(db.execute("SELECT payload FROM snapshot_events WHERE kind='snapshot_ready'").fetchone()[0])
        evidence["files"]["file.txt"] = "untrusted-payload-hash"
        db.execute("UPDATE snapshot_events SET payload=? WHERE kind='snapshot_ready'", (json.dumps(evidence),))
    events.consume_workspace(repo)
    sha = baseline(repo, "active-turn")["sha"]
    assert not baseline(repo, "checkpoint:" + sha)
    assert events.status(repo)[0]["status"] == "ready" and events.status(repo)[0]["error"]
    # Failed new S must keep the old queue and references for inspection/retry.
    with monkeypatch.context() as patch:
        patch.setattr(cp, "create_checkpoint", lambda *a, **k: {"success": False, "reason": "disk full"})
        with exclusive_request(repo, "failed-next") as lease:
            with pytest.raises(RecoveryRequired):
                begin(repo, lease, "failed-next")
    assert events.status(repo)[0]["id"] == sha
    assert cp.list_checkpoints(repo)
    (Path(repo) / "file.txt").write_bytes(b"human current baseline")
    with exclusive_request(repo, "next") as lease:
        begin(repo, lease, "next")
        events.foreground_finished(lease, safe=True)
    assert len(events.status(repo)) == 1
    assert events.status(repo)[0]["id"] != sha
    # Re-delivering completion callbacks cannot resurrect a retired run.
    events.foreground_finished(state._request_lease, safe=True)
    events._last_writer(_database(), sha)
    with _db(_database()) as db:
        assert not db.execute("SELECT 1 FROM snapshot_events WHERE run=?", (sha,)).fetchone()
    assert not baseline(repo, "checkpoint:" + sha)


def test_real_host_background_reply_then_next_turn_has_no_unsealed_dead_end(repo, monkeypatch):
    from research_agent.agent import run_agent
    from research_agent.kernel import AgentKernel, KernelResult, ToolCall
    from research_agent.models import AgentState
    from research_agent.tools import ToolRegistry, get_registry
    from research_agent.tools.builtin import filesystem as fs
    from research_agent.sandbox import SandboxConfig
    monkeypatch.setattr(ToolRegistry, "_instance", None)
    monkeypatch.setattr(fs, "get_sandbox_config", lambda: SandboxConfig(backend="local"))
    monkeypatch.setattr("research_agent.agent._tool_approval_hook", lambda *a, **k: None)
    entered, release = Event(), Event()
    def command(command, cwd, timeout, token):
        entered.set()
        while not release.wait(0.01):
            token.check()
        (Path(cwd) / "file.txt").write_bytes(b"background result")
        return {"success": True, "returncode": 0}
    monkeypatch.setattr("research_agent.process_control.run_local", command)
    class BackgroundKernel(AgentKernel):
        id = "snapshot-outbox-host-test"
        def run(self, host, request):
            assert host.execute(ToolCall("background", "shell_exec", {"command": "echo test", "background": True})).success
            return KernelResult("background started")
    class NextKernel(AgentKernel):
        id = "snapshot-outbox-next-test"
        def run(self, host, request):
            return KernelResult("next turn")
    try:
        result = run_agent("start background", None, AgentState(), workspace_dir=repo, chat_id="real-host", kernel=BackgroundKernel())
        assert entered.wait(3)
        assert result.termination_reason != "recovery_required", result.error
        old_ref = result._last_checkpoint
        assert not baseline(repo, "checkpoint:" + baseline(repo, "active-turn")["sha"])
    finally:
        release.set()
        get_registry().background_tasks.drain()
    assert events.status(repo)[0]["status"] == "sealed"
    next_result = run_agent("continue", None, AgentState(), workspace_dir=repo, chat_id="real-host", kernel=NextKernel())
    assert next_result.termination_reason != "recovery_required", next_result.error
    assert next_result._last_checkpoint != old_ref
    assert (Path(repo) / "file.txt").read_bytes() == b"background result"


def test_two_open_shared_chats_capture_baseline_at_execution_and_undo_only_latest(repo, monkeypatch):
    from research_agent.agent import run_agent
    from research_agent.kernel import AgentKernel, KernelResult, ToolCall
    from research_agent.models import AgentState
    from research_agent.tools import ToolRegistry
    from research_agent.rollback import review_undo, apply_undo
    from research_agent.session_workspace import resolve_session, bound_session
    monkeypatch.setattr(ToolRegistry, "_instance", None)
    monkeypatch.setattr("research_agent.agent._tool_approval_hook", lambda *a, **k: None)
    # Both windows exist before A modifies anything; neither gets a snapshot yet.
    assert resolve_session(repo, "window-a") is None
    assert resolve_session(repo, "window-b") is None
    state_a, state_b = AgentState(), AgentState()
    class Writer(AgentKernel):
        id = "shared-serial-writer"
        def __init__(self, content):
            self.content = content
        def run(self, host, request):
            observed = host.execute(ToolCall("read", "file_read", {"path": "file.txt"}))
            assert observed.success, observed.data
            result = host.execute(ToolCall("write", "file_write", {"expected_version": observed.data["version"], "path": "file.txt", "content": self.content}))
            assert result.success, result
            return KernelResult("done")
    path = Path(repo) / "file.txt"
    head = cp._state(repo)["head"]
    run_agent("A", None, state_a, workspace_dir=repo, chat_id="window-a", kernel=Writer("A result\n"))
    assert state_a.termination_reason != "recovery_required", state_a.error
    assert path.read_text() == "A result\n"
    git(repo, "add", "file.txt")
    index = cp._index_state(repo)["data"]
    path.write_bytes(b"A result\nhuman between rounds\n")
    extra = Path(repo) / "human.txt"
    extra.write_bytes(b"untracked human input")
    run_agent("B", None, state_b, workspace_dir=repo, chat_id="window-b", kernel=Writer("B result\n"))
    assert state_b.termination_reason != "recovery_required", state_b.error
    assert path.read_text() == "B result\n"
    assert bound_session("window-a") is None and bound_session("window-b") is None
    assert cp._state(repo)["head"] == head  # No automatic commit/branch merge.
    assert cp._index_state(repo)["data"] == index
    review = review_undo(repo, state_b._last_checkpoint)
    assert review["success"], review
    assert apply_undo(repo, review_token=review["review_token"])["success"]
    assert path.read_bytes() == b"A result\nhuman between rounds\n"
    assert extra.read_bytes() == b"untracked human input"
    assert cp._index_state(repo)["data"] == index
    assert not cp.review_checkpoint(repo, state_a._last_checkpoint)["success"]
