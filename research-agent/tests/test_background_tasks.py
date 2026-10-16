import asyncio
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
from threading import Event

import pytest

from research_agent.background_tasks import BackgroundTasks
from research_agent.execution import CancellationToken, Cancelled, exclusive_request
from research_agent.models import AgentState
from research_agent.tools import ToolRegistry
from research_agent.tools.builtin import _plugins


@pytest.fixture
def registry(monkeypatch):
    from research_agent import tools
    from research_agent.tools.builtin import filesystem as fs
    from research_agent.sandbox import SandboxConfig
    reg = ToolRegistry()
    monkeypatch.setattr(tools, "get_registry", lambda: reg)
    monkeypatch.setattr(fs, "get_sandbox_config", lambda: SandboxConfig(backend="local"))
    for plugin in _plugins():
        if plugin.id == "shell":
            reg.install_plugin(plugin, apply_enabled=False)
    yield reg
    reg.background_tasks.shutdown()


def worker(entered, release):
    def invoke(token):
        entered.set()
        while not release.wait(0.01):
            token.check()
        return {"success": True, "stdout": "finished", "returncode": 0}
    return invoke


def start(manager, tmp_path, chat="chat", execution="execution", invoke=None, **kwargs):
    entered, release = Event(), Event()
    descriptor = manager.start(chat=chat, execution=execution, workspace=str(tmp_path),
                               command="test-command", backend="local", leases=ExitStack(),
                               invoke=invoke or worker(entered, release), **kwargs)
    if invoke is None:
        assert entered.wait(3)
    return descriptor, release


def test_cross_process_control_store_cancels_only_the_target_session(registry, tmp_path):
    manager = registry.background_tasks
    descriptor, _ = start(manager, tmp_path)
    other_controller = BackgroundTasks()
    assert other_controller.list("other") == []
    assert not other_controller.cancel(chat="other", task_id=descriptor["task_id"])
    assert other_controller.cancel(chat="chat", task_id=descriptor["task_id"]) == [descriptor["task_id"]]
    manager.drain()
    assert manager.list("chat")[0]["status"] == "cancelled"
    assert not (tmp_path / "tasks").exists()


def test_finished_foreground_execution_can_still_cancel_background(registry, tmp_path):
    from research_agent.server import cancel_execution
    descriptor, _ = start(registry.background_tasks, tmp_path)
    result = asyncio.run(cancel_execution("execution"))
    assert result["background_tasks"] == [descriptor["task_id"]]
    registry.background_tasks.drain()
    assert registry.background_tasks.list("chat")[0]["status"] == "cancelled"


def test_scheduling_captures_original_parent_token(registry, monkeypatch, tmp_path):
    from research_agent.tools.builtin import filesystem as fs
    entered, release = Event(), Event()
    monkeypatch.setattr("research_agent.process_control.run_local", lambda command, cwd, timeout, token: worker(entered, release)(token))
    original = CancellationToken()
    state = AgentState(workspace_dir=str(tmp_path), active_chat_id="captured")
    state._cancellation = original
    state._execution_id = "original-execution"
    fs._handle_shell_exec({"command": "test", "background": True}, None, state, lambda *args: None)
    assert entered.wait(3)
    state._cancellation = CancellationToken()  # reused AgentState for a later turn
    state._execution_id = "new-execution"
    original.cancel()
    registry.background_tasks.drain()
    task = registry.background_tasks.list("captured")[0]
    assert task["status"] == "cancelled" and task["execution"] == "original-execution"
    state._cancellation.check()


def test_close_session_cancels_and_drains_only_its_own_background(registry, tmp_path):
    first, second = registry.session_scope("first").acquire(), registry.session_scope("second").acquire()
    first.__enter__()
    second.__enter__()
    one, two = ExitStack(), ExitStack()
    one.callback(first.__exit__, None, None, None)
    two.callback(second.__exit__, None, None, None)
    entered_a, entered_b, release_a, release_b = Event(), Event(), Event(), Event()
    manager = registry.background_tasks
    manager.start(chat="first", execution="one", workspace=str(tmp_path), command="one", backend="local",
                  leases=one, invoke=worker(entered_a, release_a))
    manager.start(chat="second", execution="two", workspace=str(tmp_path), command="two", backend="local",
                  leases=two, invoke=worker(entered_b, release_b))
    assert entered_a.wait(3) and entered_b.wait(3)
    registry.close_session("first", timeout=3)
    assert manager.list("first")[0]["status"] == "cancelled"
    assert manager.list("second")[0]["status"] == "running"
    with pytest.raises(RuntimeError, match="admission"):
        start(manager, tmp_path, chat="first")
    registry.close_session("second", timeout=3)
    registry.reopen_session("first")
    descriptor, release = start(manager, tmp_path, chat="first")
    release.set()
    manager.drain()
    assert manager.list("first", descriptor["task_id"])[0]["status"] == "done"


def test_plugin_drain_cancels_worker_and_releases_workspace_lease(registry, monkeypatch, tmp_path):
    from research_agent.tools.builtin import filesystem as fs
    entered, release = Event(), Event()
    monkeypatch.setattr("research_agent.process_control.run_local", lambda command, cwd, timeout, token: worker(entered, release)(token))
    state = AgentState(workspace_dir=str(tmp_path), active_chat_id="plugin")
    with exclusive_request(str(tmp_path), "plugin") as lease:
        state._request_lease = lease
        fs._handle_shell_exec({"command": "test", "background": True}, None, state, lambda *args: None)
        assert entered.wait(3)
    with pytest.raises(RuntimeError):
        with exclusive_request(str(tmp_path), "other"):
            pass
    registry.disable_plugin("shell", persist=False)
    assert registry.background_tasks.list("plugin")[0]["status"] == "cancelled"
    with exclusive_request(str(tmp_path), "other"):
        pass
    registry.enable_plugin("shell", persist=False)
    descriptor, release = start(registry.background_tasks, tmp_path)
    release.set()
    registry.background_tasks.drain()


def test_drain_timeout_keeps_task_owner_until_worker_exits(registry, tmp_path):
    entered, release = Event(), Event()
    def blocked(token):
        entered.set()
        assert release.wait(5)
        return {"success": True, "returncode": 0}
    descriptor, _ = start(registry.background_tasks, tmp_path, invoke=blocked)
    assert entered.wait(3)
    try:
        with pytest.raises(TimeoutError):
            registry.background_tasks.shutdown(timeout=0)
        # Owner is alive: timeout must not turn it into a stale/completed task.
        assert registry.background_tasks.list("chat")[0]["status"] == "running"
    finally:
        release.set()
        registry.background_tasks.drain()


def test_application_lifespan_cancels_and_drains_background(registry, tmp_path):
    from research_agent.server import _lifespan, app
    async def scenario():
        async with _lifespan(app):
            start(registry.background_tasks, tmp_path)
    asyncio.run(scenario())
    assert registry.background_tasks.list("chat")[0]["status"] == "cancelled"


def test_session_request_scope_cancels_inflight_request(registry):
    entered = Event()
    token = CancellationToken()
    def request():
        with registry.request_scope("request", token):
            entered.set()
            while not token._event.wait(0.01):
                pass
            token.check()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(request)
        assert entered.wait(3)
        registry.close_session("request", timeout=3)
        with pytest.raises(Cancelled):
            future.result()


def test_process_exit_leaves_uncertain_task_and_blocks_workspace(registry, tmp_path):
    script = """
from contextlib import ExitStack
import os, sys, time
from research_agent.background_tasks import BackgroundTasks
manager = BackgroundTasks()
item = manager.start(chat='crashed', execution='crashed-run', workspace=sys.argv[1], command='unknown',
                     backend='local', leases=ExitStack(), invoke=lambda token: time.sleep(60))
print(item['task_id'], flush=True)
os._exit(0)
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src") + os.pathsep + env.get("PYTHONPATH", "")
    child = subprocess.run([sys.executable, "-c", script, str(tmp_path)], env=env,
                           capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stderr
    registry.background_tasks.recover_workspace(str(tmp_path))
    task = registry.background_tasks.list("crashed")[0]
    assert task["status"] == "recovery_required"
    with pytest.raises(RuntimeError, match="quarantined"):
        with exclusive_request(str(tmp_path), "new"):
            pass


def test_cli_queries_and_cancels_without_model_request(registry, tmp_path):
    import json
    from click.testing import CliRunner
    from research_agent.cli import main
    item, _ = start(registry.background_tasks, tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["tasks", "list", "--chat", "chat"])
    assert result.exit_code == 0 and json.loads(result.output)[0]["id"] == item["task_id"]
    result = runner.invoke(main, ["tasks", "cancel", "--chat", "chat", "--task", item["task_id"]])
    assert result.exit_code == 0
    registry.background_tasks.drain()
    assert registry.background_tasks.list("chat")[0]["status"] == "cancelled"


def test_closed_session_is_enforced_across_host_registries(registry):
    other = ToolRegistry()
    original = CancellationToken()
    with other.request_scope("shared-session", original) as token:
        with pytest.raises(TimeoutError):
            # Simulate a request in another host still owning the global chat lease.
            with exclusive_request("shared-workspace", "shared-session"):
                registry.close_session("shared-session", timeout=0)
        with pytest.raises(Cancelled):
            token.check()
    with pytest.raises(RuntimeError, match="closed"):
        with other.request_scope("shared-session", CancellationToken()):
            pass
    registry.close_session("shared-session", timeout=1)
    registry.reopen_session("shared-session")
    with other.request_scope("shared-session", CancellationToken()) as reopened:
        reopened.check()


def test_cancellation_during_approval_publication_does_not_leave_waiter(registry, monkeypatch):
    from research_agent.agent import _tool_approval_hook
    monkeypatch.setattr("research_agent.guardrail.guardrail", lambda *args: "approval-required")
    state = AgentState()
    state._cancellation = CancellationToken()
    def emit(kind, payload):
        if kind == "confirm_required":
            state._cancellation.cancel()  # cancellation precedes pending-event registration
    with pytest.raises(Cancelled):
        _tool_approval_hook(state, "shell_exec", {"command": "echo test"}, emit)
    assert not state._pending_confirms


def test_another_os_process_can_cancel_a_background_task(registry, tmp_path):
    item, _ = start(registry.background_tasks, tmp_path)
    script = """
import sys
from research_agent.background_tasks import BackgroundTasks
ids = BackgroundTasks().cancel(chat='chat', task_id=sys.argv[1])
assert ids == [sys.argv[1]], ids
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src") + os.pathsep + env.get("PYTHONPATH", "")
    child = subprocess.run([sys.executable, "-c", script, item["task_id"]], env=env,
                           capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stderr
    registry.background_tasks.drain()
    assert registry.background_tasks.list("chat")[0]["status"] == "cancelled"


def test_lease_cleanup_failure_is_not_published_as_task_success(tmp_path):
    manager = BackgroundTasks()
    leases = ExitStack()
    def fail():
        raise OSError("cleanup failed")
    leases.callback(fail)
    item = manager.start(chat="cleanup", execution="cleanup-execution", workspace=str(tmp_path),
                         command="completed", backend="local", leases=leases,
                         invoke=lambda token: {"success": True, "returncode": 0})
    with pytest.raises(RuntimeError, match="requires reconciliation"):
        manager.drain()
    assert manager.list("cleanup", item["task_id"])[0]["status"] == "recovery_required"
    with pytest.raises(RuntimeError, match="quarantined"):
        with exclusive_request(str(tmp_path), "new"):
            pass
