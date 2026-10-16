"""Network suspension resumes complete steps, without undoing or replaying edits."""
import json
import hashlib
import os
import sys
from pathlib import Path
import subprocess
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from research_agent import agent, checkpoint as cp, checkpoint_events
from research_agent.execution import CancellationToken, Cancelled, RecoveryRequired, checkpoint
from research_agent.kernel import FunctionCallingKernel, KernelRequest, ModelResponse, ToolCall, ToolObservation
from research_agent.model_retry import ModelUnavailable, PartialResponseError, retry_model
from research_agent.models import AgentState
from research_agent.recovery import inspect_execution
from research_agent.review_baseline import baseline


def git(ws, *args):
    result = subprocess.run(["git", *args], cwd=ws, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from research_agent.tools import ToolRegistry
    monkeypatch.setattr(ToolRegistry, "_instance", None)
    monkeypatch.setattr("research_agent.runtime.MAX_TOTAL_RETRIES", 0)
    monkeypatch.setattr(agent, "_auto_validate", lambda *a: None)
    monkeypatch.setattr(agent, "_maybe_compress", lambda *a: None)
    monkeypatch.setattr(agent, "_maybe_save_episode", lambda *a, **k: None)
    monkeypatch.setattr(agent, "_stream_response", lambda *a: "done")
    monkeypatch.setattr(agent, "_tool_approval_hook", lambda *a: None)
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "test")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "core.autocrlf", "false")
    (tmp_path / "code.txt").write_bytes(b"original")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "initial")
    return str(tmp_path)


def run(ws, *, execution="network-run", state=None, callback=None):
    return agent.run_agent("edit code", None, state or AgentState(), workspace_dir=ws,
                           chat_id="network-chat", execution_id=execution, on_event=callback)


def offline(*args, **kwargs):
    raise TimeoutError("network unavailable")


def test_first_model_disconnect_is_durable_and_does_not_consume_more_rounds(workspace, monkeypatch):
    monkeypatch.setattr(agent, "_call_llm_with_tools", offline)
    original = AgentState()
    original.constraints = ["Do not delete user files"]
    original.caps["remaining_budget"] = 7
    paused = run(workspace, state=original)
    ref = paused._last_checkpoint
    assert paused.termination_reason == "waiting_network" and not paused.error
    info = inspect_execution("network-run")
    assert (info["phase"], info["rounds"], info["status"]) == ("select", 1, "waiting_network")
    assert checkpoint_events.status(workspace)[0]["status"] == "suspended"
    assert not baseline(workspace, "checkpoint:" + baseline(workspace, "active-turn")["sha"])
    monkeypatch.setattr(agent, "_call_llm_with_tools", lambda *a: {})
    resumed = run(workspace)
    assert resumed.final_response == "done" and resumed.round_count == 1
    assert resumed._last_checkpoint == ref
    assert resumed.constraints == ["Do not delete user files"]
    assert resumed.caps["remaining_budget"] == 7
    assert checkpoint_events.status(workspace)[0]["status"] == "sealed"


def test_disconnect_after_write_reuses_context_and_keeps_whole_turn_undo(workspace, monkeypatch):
    calls, approvals = [], []
    def model(*args):
        calls.append(args[1])
        if len(calls) == 1:
            return {"tool_calls": [
                {"id": "read-first", "name": "file_read", "params": {"path": "code.txt"}},
                {"id": "write-once", "name": "file_write", "params": {"expected_version": hashlib.sha256(b"original").hexdigest(), "path": "code.txt", "content": "agent change"}}]}
        raise TimeoutError()
    monkeypatch.setattr(agent, "_call_llm_with_tools", model)
    monkeypatch.setattr(agent, "_tool_approval_hook", lambda *a: approvals.append(a[1]))
    paused = run(workspace)
    assert paused.termination_reason == "waiting_network"
    assert (Path(workspace) / "code.txt").read_text() == "agent change"
    resumed_context = []
    monkeypatch.setattr(agent, "_call_llm_with_tools", lambda llm, messages, *a: resumed_context.extend(messages) or {})
    resumed = run(workspace)
    assert resumed._last_checkpoint == paused._last_checkpoint
    assert resumed.round_count == 2 and approvals == ["file_read", "file_write"]
    assert sum(m["role"] == "tool" for m in resumed_context) == 2
    from research_agent.rollback import review_undo, apply_undo
    review = review_undo(workspace, resumed._last_checkpoint)
    assert review["success"], review
    assert apply_undo(workspace, review_token=review["review_token"])["success"]
    assert (Path(workspace) / "code.txt").read_bytes() == b"original"


def test_partial_answer_resumes_answer_only_and_replaces_published_prefix(workspace, monkeypatch):
    selects, emitted = [], []
    monkeypatch.setattr(agent, "_call_llm_with_tools", lambda *a: selects.append(1) or {})
    def partial(llm, messages, emit):
        emit("reply", {"text": "half an answer"})
        raise PartialResponseError("half an answer", TimeoutError())
    monkeypatch.setattr(agent, "_stream_response", partial)
    paused = run(workspace)
    assert paused.final_response == "half an answer"
    assert inspect_execution("network-run")["phase"] == "answer"
    monkeypatch.setattr(agent, "_stream_response", lambda llm, messages, emit: emit("reply", {"text": "complete"}) or "complete")
    resumed = run(workspace, callback=lambda kind, data: emitted.append((kind, data)))
    assert resumed.final_response == "complete" and selects == [1]
    kinds = [kind for kind, data in emitted]
    assert kinds.index("reply_reset") < kinds.index("reply")


def test_human_edit_during_disconnect_creates_segment_and_requires_reread(workspace, monkeypatch):
    monkeypatch.setattr(agent, "_call_llm_with_tools", offline)
    paused = run(workspace)
    path = Path(workspace) / "code.txt"
    path.write_bytes(b"human updated")
    git(workspace, "add", "code.txt")
    index = cp._index_state(workspace)["data"]
    context = []
    monkeypatch.setattr(agent, "_call_llm_with_tools", lambda llm, messages, *a: context.extend(messages) or {})
    resumed = run(workspace)
    assert resumed._last_checkpoint != paused._last_checkpoint
    assert any("重新读取相关文件" in str(m.get("content", "")) for m in context)
    assert path.read_bytes() == b"human updated"
    assert cp._index_state(workspace)["data"] == index


def test_new_task_retires_old_execution_and_old_resume_never_runs_model(workspace, monkeypatch):
    monkeypatch.setattr(agent, "_call_llm_with_tools", offline)
    run(workspace)
    monkeypatch.setattr(agent, "_call_llm_with_tools", lambda *a: {})
    latest = run(workspace, execution="new-task")
    active = baseline(workspace, "active-turn")
    monkeypatch.setattr(agent, "_call_llm_with_tools", lambda *a: pytest.fail("stale execution resumed"))
    stale = run(workspace)
    assert stale.termination_reason == "recovery_required"
    assert baseline(workspace, "active-turn") == active
    assert cp.list_checkpoints(workspace)[0]["ref"] == latest._last_checkpoint
    assert inspect_execution("network-run")["status"] == "superseded"


def test_real_kernel_replay_skips_completed_effect_and_approval(workspace, monkeypatch):
    from research_agent.runtime import RuntimeContext, RuntimeHost
    from research_agent.tools import get_registry
    from research_agent.tools.schema import ToolSchema, ToolResult
    state, effects, approvals = AgentState(), [], []
    state._execution_id = "crash-before-loop-save"
    registry = get_registry()
    registry.register(ToolSchema("effect", "effect", {"type": "object"}, lambda *a: effects.append(1) or ToolResult.ok()))
    call = ToolCall("id", "effect", {})
    host = RuntimeHost(RuntimeContext(None, "task", state, registry=registry,
        pre_tool_hook=lambda *a: approvals.append(1)))
    assert host.execute(call).success
    assert host.execute(call).success
    assert effects == [1] and approvals == [1]


def test_automatic_recovery_is_bounded_and_keeps_one_execution(workspace, monkeypatch):
    attempts = []
    def model(*a):
        attempts.append(1)
        if len(attempts) == 1:
            raise TimeoutError()
        return {}
    monkeypatch.setattr(agent, "_call_llm_with_tools", model)
    result = agent.run_agent_resilient("edit code", None, AgentState(), workspace_dir=workspace,
        chat_id="network-chat", execution_id="auto", recovery_delays=(0,))
    assert result.final_response == "done" and result.round_count == 1
    assert len(attempts) == 2 and len(checkpoint_events.status(workspace)) == 1
    monkeypatch.setattr(agent, "_call_llm_with_tools", offline)
    result = agent.run_agent_resilient("edit code", None, AgentState(), workspace_dir=workspace,
        chat_id="network-chat", execution_id="still-offline", recovery_delays=(0, 0))
    assert result.termination_reason == "waiting_network"
    assert inspect_execution("still-offline")["rounds"] == 1


def test_retry_wait_can_be_cancelled_without_another_model_call():
    token, entered, done = CancellationToken(), Event(), Event()
    calls = []
    def wait(seconds):
        entered.set()
        token.wait(seconds)
    def invoke():
        calls.append(1)
        raise TimeoutError()
    def worker():
        try:
            retry_model(invoke, None, delays=(60,), wait=wait)
        except Cancelled:
            done.set()
    thread = Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(3)
        token.cancel()
        assert done.wait(2)
    finally:
        token.cancel()
        thread.join(3)
    assert calls == [1]


def test_permanent_model_error_does_not_enter_network_wait(workspace, monkeypatch):
    class Unauthorized(Exception):
        status_code = 401
    monkeypatch.setattr(agent, "_call_llm_with_tools", lambda *a: (_ for _ in ()).throw(Unauthorized("bad key")))
    result = run(workspace)
    assert result.termination_reason == "model_error"
    assert inspect_execution("network-run")["status"] != "waiting_network"


def test_process_exit_after_committed_tool_result_replays_without_write_or_approval(workspace, monkeypatch):
    script = '''
import os, sys, hashlib
from research_agent import agent
from research_agent.models import AgentState
agent._call_llm_with_tools = lambda *a: {'tool_calls': [{'id': 'read-first', 'name': 'file_read', 'params': {'path': 'code.txt'}}, {'id': 'write-once', 'name': 'file_write', 'params': {'expected_version': hashlib.sha256(b'original').hexdigest(), 'path': 'code.txt', 'content': 'before crash'}}]}
agent._tool_approval_hook = lambda *a: None
agent._auto_validate = lambda state, name, *a: os._exit(17) if name == "file_write" else None
agent.run_agent('edit code', None, AgentState(), workspace_dir=sys.argv[1], chat_id='network-chat', execution_id='network-run')
'''
    env = dict(os.environ, PYTHONPATH=str(Path(agent.__file__).resolve().parents[1]))
    result = subprocess.run([sys.executable, "-c", script, workspace], env=env, capture_output=True, timeout=40)
    assert result.returncode == 17, result.stderr
    path = Path(workspace) / "code.txt"
    assert path.read_text() == "before crash"
    modified = path.stat().st_mtime_ns
    old = baseline(workspace, "active-turn")["ref"]
    monkeypatch.setattr(agent, "_call_llm_with_tools", lambda *a: {})
    monkeypatch.setattr(agent, "_tool_approval_hook", lambda *a: pytest.fail("completed tool reapproved"))
    resumed = run(workspace)
    assert resumed.final_response == "done", resumed.error
    assert resumed._last_checkpoint == old and path.stat().st_mtime_ns == modified
    assert [c["status"] for c in inspect_execution("network-run")["calls"] if c["call_id"] == "write-once"] == ["completed"]


def test_unknown_tool_outcome_stops_before_model_or_approval(workspace, monkeypatch):
    from research_agent.network_recovery import identity
    monkeypatch.setattr(agent, "_call_llm_with_tools", offline)
    paused = run(workspace)
    key = identity(paused, "edit code", workspace, "network-chat")
    saved = checkpoint("network-run", key)
    saved["phase"] = "tools"
    saved["pending"] = [{"id": "uncertain", "name": "file_write", "params": {"expected_version": "absent", "path": "code.txt", "content": "uncertain"}}]
    checkpoint("network-run", key, saved)
    from research_agent.recovery import database
    with database() as db:
        db.execute("CREATE TABLE IF NOT EXISTS calls(run TEXT, call TEXT, signature TEXT, status TEXT, result TEXT, PRIMARY KEY(run,call))")
        db.execute("INSERT INTO calls VALUES(?,?,?,'started',NULL)",
                   ("network-run", "uncertain", json.dumps(["file_write", saved["pending"][0]["params"]])))
        db.commit()
    monkeypatch.setattr(agent, "_call_llm_with_tools", lambda *a: pytest.fail("unknown effect bypassed"))
    resumed = run(workspace)
    assert resumed.termination_reason == "recovery_required"
    assert (Path(workspace) / "code.txt").read_bytes() == b"original"


def test_resume_cannot_reset_round_limit():
    class Host:
        first = True
        def checkpoint(self, payload=None):
            return checkpoint("round-budget", "same", payload)
        def prepare(self):
            return [{"role": "user", "content": "task"}]
        def select(self, messages):
            if self.first:
                self.first = False
                raise ModelUnavailable()
            return ModelResponse(tool_calls=(ToolCall("call", "test", {}),))
        def execute(self, call):
            return ToolObservation(True, {})
        def after_tool(self, *args):
            pass
        def answer(self, messages, *, fallback=False):
            assert fallback
            return "budget exhausted"
        def set_round(self, number):
            pass
        def emit(self, *args):
            pass
    host = Host()
    with pytest.raises(ModelUnavailable):
        FunctionCallingKernel().run(host, KernelRequest("task", 1))
    result = FunctionCallingKernel().run(host, KernelRequest("task", 100))
    assert result.reason.value == "round_limit" and result.rounds == 1


def test_execution_status_api_exposes_saved_cursor(workspace, monkeypatch):
    monkeypatch.setattr(agent, "_call_llm_with_tools", offline)
    run(workspace)
    from fastapi.testclient import TestClient
    from research_agent.server import app
    with TestClient(app) as client:
        result = client.get("/api/executions/network-run")
        assert result.status_code == 200
        assert result.json()["status"] == "waiting_network"
        assert result.json()["phase"] == "select"
        assert client.get("/api/executions/missing").status_code == 404


@pytest.mark.parametrize("failure", [429, 503, "disconnect"])
def test_real_provider_transport_recovers_from_local_http_fault(workspace, monkeypatch, failure):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from research_agent.llm import LiteLLMProvider
    from research_agent.model_adapter import call_with_tools
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if len(requests) == 1 and failure == "disconnect":
                self.connection.close()
                return
            status = failure if len(requests) == 1 else 200
            body = ({"error": {"message": "temporary outage", "type": "server_error"}} if status != 200 else
                {"id": "local-response", "object": "chat.completion", "created": 1, "model": "local-test",
                 "choices": [{"index": 0, "message": {"role": "assistant", "content": "ready"}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}})
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    provider = LiteLLMProvider(model="openai/local-test", api_key="local-test-only",
        api_base=f"http://127.0.0.1:{server.server_port}/v1")
    import httpx
    from openai import OpenAI
    # Windows system proxy settings must not route this loopback fault server
    # through a remote gateway. Keep the real SDK and LiteLLM parser in use.
    client = OpenAI(api_key="local-test-only", base_url=provider.api_base,
                    http_client=httpx.Client(trust_env=False), max_retries=0, timeout=3)
    provider._kwargs.update(timeout=3, num_retries=0, client=client)
    monkeypatch.setattr(agent, "_call_llm_with_tools", call_with_tools)
    try:
        first = agent.run_agent("edit code", provider, AgentState(), workspace_dir=workspace,
            chat_id="network-chat", execution_id="http-fault")
        assert first.termination_reason == "waiting_network", first.error
        second = agent.run_agent("edit code", provider, AgentState(), workspace_dir=workspace,
            chat_id="network-chat", execution_id="http-fault")
        from research_agent.network_recovery import identity
        saved = checkpoint("http-fault", identity(second, "edit code", workspace, "network-chat"))
        assert second.final_response == "done", (second.error, saved.get("interruption"), len(requests))
        assert second.round_count == 1 and first._last_checkpoint == second._last_checkpoint
        assert len(requests) == 2
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(3)


@pytest.mark.parametrize("finish", [None, "stop"])
def test_stream_requires_completion_boundary_and_always_closes(monkeypatch, finish):
    from research_agent.model_adapter import stream_once, ModelSettings
    from research_agent.model_retry import stream_with_retry
    closed, emitted = [], []
    class Stream:
        def __iter__(self):
            yield SimpleNamespace(usage=None, choices=[SimpleNamespace(
                finish_reason=finish, delta=SimpleNamespace(content="published"))])
        def close(self):
            closed.append(True)
    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(completion=lambda **kwargs: Stream()))
    provider = SimpleNamespace(model="local-test", api_key="test", _kwargs={})
    def invoke(emit):
        return stream_once(provider, [], emit, settings=ModelSettings(0, 0, None))
    if finish is None:
        with pytest.raises(PartialResponseError) as failure:
            stream_with_retry(invoke, lambda kind, data: emitted.append((kind, data)))
        assert failure.value.partial == "published"
    else:
        assert stream_with_retry(invoke, lambda kind, data: emitted.append((kind, data))) == "published"
    assert closed == [True] and len(emitted) == 1


@pytest.mark.parametrize("point", ["before-cursor", "after-cursor", "after-publish", "after-retire"])
def test_segment_handoff_survives_process_exit_at_every_boundary(workspace, monkeypatch, point):
    monkeypatch.setattr(agent, "_call_llm_with_tools", offline)
    old = run(workspace)._last_checkpoint
    (Path(workspace) / "code.txt").write_bytes(b"human between attempts")
    script = '''
import os, sys, hashlib
from research_agent import agent, checkpoint as cp, network_recovery as recovery
from research_agent.models import AgentState
from research_agent import git_maintenance
git_maintenance.launch_worker = lambda *a: None
old, point = sys.argv[2:]
original = recovery.checkpoint
def cursor(run, key, payload=None):
    switching = payload is not None and payload.get('workspace_checkpoint') != old
    if switching and point == 'before-cursor':
        os._exit(17)
    result = original(run, key, payload)
    if switching and point == 'after-cursor':
        os._exit(17)
    return result
recovery.checkpoint = cursor
if point == 'after-publish':
    cp._finish_snapshot_transition = lambda *a: os._exit(17)
if point == 'after-retire':
    cp._cleanup_undo_artifacts = lambda *a, **k: os._exit(17)
agent._call_llm_with_tools = lambda *a: {}
agent._stream_response = lambda *a: 'done'
agent.run_agent('edit code', None, AgentState(), workspace_dir=sys.argv[1], chat_id='network-chat', execution_id='network-run')
'''
    env = dict(os.environ, PYTHONPATH=str(Path(agent.__file__).resolve().parents[1]))
    crashed = subprocess.run([sys.executable, "-c", script, workspace, old, point], env=env,
                             capture_output=True, timeout=40)
    assert crashed.returncode == 17, crashed.stderr
    assert baseline(workspace, "snapshot-transition")
    if point != "after-retire":
        assert old in [item["ref"] for item in cp.list_checkpoints(workspace)]
    monkeypatch.setattr(agent, "_call_llm_with_tools", lambda *a: {})
    recovered = run(workspace)
    assert recovered.final_response == "done", recovered.error
    assert not baseline(workspace, "snapshot-transition")
    assert [item["ref"] for item in cp.list_checkpoints(workspace)] == [recovered._last_checkpoint]
    assert (Path(workspace) / "code.txt").read_bytes() == b"human between attempts"


def test_cancellation_while_waiting_is_durable_and_does_not_retry(workspace, monkeypatch):
    calls, token = [], CancellationToken()
    def model(*a):
        calls.append(1)
        raise TimeoutError()
    monkeypatch.setattr(agent, "_call_llm_with_tools", model)
    def emit(kind, data):
        if kind == "network_wait":
            token.cancel()
    result = agent.run_agent_resilient("edit code", None, AgentState(), workspace_dir=workspace,
        chat_id="network-chat", execution_id="cancel-network", cancellation=token, on_event=emit,
        recovery_delays=(60,))
    assert result.termination_reason == "cancelled"
    assert inspect_execution("cancel-network")["status"] == "cancelled"
    assert calls == [1]
    from research_agent.execution import checkpoint_payload, cancel_checkpoint
    saved = checkpoint_payload("cancel-network")
    assert saved["phase"] == "select" and saved["rounds"] == 1
    assert not cancel_checkpoint("cancel-network", only_waiting=True)
    assert checkpoint_payload("cancel-network") == saved


@pytest.mark.parametrize("failure", [TimeoutError, ModelUnavailable])
def test_tool_network_failure_is_an_observation_not_task_suspension(workspace, monkeypatch, failure):
    from research_agent.tools import get_registry
    from research_agent.tools.schema import ToolSchema
    registry, invocations = get_registry(), []
    def tool(*a):
        invocations.append(1)
        raise failure("network down")
    registry.register(ToolSchema("remote_read", "read", {"type": "object"}, tool, side_effect=False))
    calls = []
    def model(llm, messages, *a):
        calls.append(1)
        if len(calls) == 1:
            return {"tool_calls": [{"id": "read-once", "name": "remote_read", "params": {}}]}
        observed = json.loads(next(m["content"] for m in messages if m["role"] == "tool"))
        assert observed["error_code"] == "network_interrupted"
        assert observed["outcome"] == "failed" and observed["retryable"]
        return {}
    monkeypatch.setattr(agent, "_call_llm_with_tools", model)
    result = run(workspace)
    assert result.termination_reason == "completed" and result.final_response == "done"
    assert invocations == [1]
    assert next(c for c in inspect_execution("network-run")["calls"] if c["call_id"] == "read-once")["status"] == "completed"


@pytest.mark.parametrize("confirmed", [False, True])
def test_side_effect_network_failure_requires_explicit_outcome(confirmed):
    from research_agent.tools import ToolRegistry
    from research_agent.tools.schema import ToolSchema, ToolNetworkError
    registry, state, invoked = ToolRegistry(), AgentState(), []
    state._execution_id, state._current_tool_id = "effect-network", "effect"
    def tool(*a):
        invoked.append(1)
        raise ToolNetworkError("connection lost", confirmed_failed=confirmed)
    registry.register(ToolSchema("remote_effect", "effect", {"type": "object"}, tool))
    if confirmed:
        assert registry.dispatch("remote_effect", {}, None, state, None).data["error_code"] == "network_interrupted"
        assert not registry.dispatch("remote_effect", {}, None, state, None).success
    else:
        with pytest.raises(RecoveryRequired):
            registry.dispatch("remote_effect", {}, None, state, None)
        with pytest.raises(RecoveryRequired):
            registry.dispatch("remote_effect", {}, None, state, None)
    assert invoked == [1]


def test_builtin_fetch_timeout_returns_structured_network_failure(monkeypatch):
    import httpx
    from research_agent.tools.builtin import network
    monkeypatch.setattr(network, "_web_cfg", lambda: {"allow_private": True, "fetch_backends": ["direct"]})
    class OfflineClient:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            raise httpx.ReadTimeout("lost connection")
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(network.httpx, "Client", OfflineClient)
    result = network._handle_web_fetch({"url": "https://example.invalid"}, None, AgentState(), lambda *a: None)
    assert not result.success
    assert result.data["error_code"] == "network_interrupted"
    assert result.data["outcome"] == "failed"


def test_failed_cursor_commit_preserves_previous_snapshot(workspace, monkeypatch):
    monkeypatch.setattr(agent, "_call_llm_with_tools", offline)
    old = run(workspace)._last_checkpoint
    (Path(workspace) / "code.txt").write_bytes(b"human update")
    from research_agent import network_recovery as recovery
    real_checkpoint = recovery.checkpoint
    def disk_full(run_id, key, payload=None):
        if payload is not None and payload.get("workspace_checkpoint") != old:
            raise OSError("disk full")
        return real_checkpoint(run_id, key, payload)
    with monkeypatch.context() as patch:
        patch.setattr(recovery, "checkpoint", disk_full)
        failed = run(workspace)
    assert failed.termination_reason == "recovery_required"
    assert baseline(workspace, "active-turn")["ref"] == old
    assert old in [item["ref"] for item in cp.list_checkpoints(workspace)]
    monkeypatch.setattr(agent, "_call_llm_with_tools", lambda *a: {})
    assert run(workspace).final_response == "done"


def test_handoff_recovery_never_discards_partial_rollback(workspace, monkeypatch):
    monkeypatch.setattr(agent, "_call_llm_with_tools", offline)
    run(workspace)
    active = baseline(workspace, "active-turn")
    baseline(workspace, "snapshot-transition", {"previous": active, "next": active})
    partial = {"status": "partial", "target": active["sha"], "backup": active["ref"]}
    baseline(workspace, "checkpoint-rollback", partial)
    refs = cp.list_checkpoints(workspace)
    from research_agent.execution import exclusive_request
    with exclusive_request(workspace, "recovery-check"):
        with pytest.raises(RecoveryRequired, match="partial rollback"):
            cp.recover_snapshot_transition(workspace)
    assert baseline(workspace, "checkpoint-rollback") == partial
    assert baseline(workspace, "snapshot-transition")
    assert cp.list_checkpoints(workspace) == refs
