"""Opt-in, fail-closed real Docker acceptance. No automatic image downloads.

RESEARCH_AGENT_DOCKER_TESTS=1 py -3 -m pytest tests/test_docker_real.py -v
Requires the configured RESEARCH_AGENT_DOCKER_IMAGE (default python:3.11-slim).
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from research_agent import sandbox
from research_agent.docker_ownership import reconcile_containers, reconcile_before_request
from research_agent.execution import CancellationToken, Cancelled
from research_agent.models import AgentState
from research_agent.process_control import run_docker
from research_agent.tools.builtin.git_inspect import inspect_git

pytestmark = pytest.mark.skipif(os.environ.get("RESEARCH_AGENT_DOCKER_TESTS") != "1",
                                reason="real Docker acceptance is opt-in")


def docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          errors="replace", timeout=30)


@pytest.fixture(scope="module")
def docker_engine():
    info = docker("info", "--format", "{{.OSType}}")
    assert info.returncode == 0 and info.stdout.strip() == "linux", info.stderr
    image = os.environ.get("RESEARCH_AGENT_DOCKER_IMAGE", "python:3.11-slim")
    present = docker("image", "inspect", image)
    assert present.returncode == 0, f"Required image is not local: {image}; pull it explicitly before acceptance"
    return image


@pytest.fixture
def real_config(monkeypatch, docker_engine):
    cfg = sandbox.SandboxConfig(backend="docker", image=docker_engine, max_containers=2)
    monkeypatch.setattr(sandbox, "get_sandbox_config", lambda: cfg)
    sandbox.reset_docker_cache()
    yield cfg
    sandbox.close_all_sessions()
    reconcile_containers(cfg)


def test_real_run_reuse_limits_shared_mount_and_end_cleanup(real_config, tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "reference.txt").write_text("reference", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    real_config.shared_mounts = [sandbox.SharedMount(str(shared), "/shared/reference")]
    state = AgentState()
    session = sandbox.get_session(state, str(work), real_config)
    assert not session._started  # no command, no container
    try:
        assert session.exec("echo first > /work/result.txt", 10)["success"]
        name = session._container
        assert sandbox.get_session(state, str(work), real_config) is session
        assert session.exec("cat /work/result.txt", 10)["stdout"].strip() == "first"
        config = json.loads(docker("inspect", name).stdout)[0]
        assert config["HostConfig"]["NetworkMode"] == "none"
        assert config["HostConfig"]["ReadonlyRootfs"]
        assert config["HostConfig"]["Memory"] > 0 and config["HostConfig"]["NanoCpus"] > 0
        assert not session.exec("touch /root/forbidden", 10)["success"]
        assert not session.exec("echo changed > /shared/reference/reference.txt", 10)["success"]
    finally:
        sandbox.end_run(state)
    assert (work / "result.txt").read_text().strip() == "first"
    assert (shared / "reference.txt").read_text() == "reference"
    assert docker("inspect", name).returncode != 0


@pytest.mark.parametrize("cancel", [False, True])
def test_real_foreground_timeout_and_cancel_remove_container(real_config, tmp_path, cancel):
    session = sandbox.create_session(str(tmp_path), real_config)
    session.start()
    name = session._container
    token = CancellationToken()
    timer = threading.Timer(0.3, token.cancel) if cancel else None
    try:
        if timer:
            timer.start()
        with pytest.raises(Cancelled if cancel else subprocess.TimeoutExpired):
            run_docker("sleep 60", str(tmp_path), 30 if cancel else 0.3, token, session=session)
        assert docker("inspect", name).returncode != 0
    finally:
        if timer:
            timer.cancel()
            timer.join()
        session.close()


def test_real_background_oneshot_cleans_on_timeout(real_config, tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        run_docker("sleep 60", str(tmp_path), 0.5, CancellationToken())
    assert reconcile_containers(real_config) == []
    release = sandbox.acquire_docker_slot(real_config)
    release()


def test_real_host_crash_recovery_preserves_another_live_window(real_config, tmp_path):
    alive = sandbox.create_session(str(tmp_path), real_config)
    alive.start()
    try:
        script = """
import json, os, sys
from research_agent.sandbox import SandboxConfig, create_session
session = create_session(sys.argv[1], SandboxConfig(backend='docker', image=sys.argv[2], max_containers=2))
session.start()
print(json.dumps({'name': session._container}), flush=True)
os._exit(0)  # deliberate crash: no finally/atexit cleanup
"""
        env = dict(os.environ)
        src = str(Path(__file__).resolve().parents[1] / "src")
        env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
        child = subprocess.run([sys.executable, "-c", script, str(tmp_path), real_config.image],
                               env=env, capture_output=True, text=True, timeout=90)
        assert child.returncode == 0, child.stderr
        orphan = json.loads(child.stdout)["name"]
        assert docker("inspect", orphan).returncode == 0
        assert reconcile_before_request()
        assert docker("inspect", orphan).returncode != 0
        assert alive.exec("echo still-alive", 10)["success"]
    finally:
        alive.close()


def test_real_container_edits_worktree_and_host_git_reads_it(real_config, tmp_path):
    source, worktree = tmp_path / "source", tmp_path / "session"
    source.mkdir()
    def git(*args):
        subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True)
    git("init")
    git("config", "user.name", "Docker Acceptance")
    git("config", "user.email", "docker@example.invalid")
    (source / "code.txt").write_text("original\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "initial")
    git("worktree", "add", "-b", "codex/docker", str(worktree))
    session = sandbox.create_session(str(worktree), real_config)
    try:
        assert session.exec("echo updated > /work/code.txt", 10)["success"]
        status = inspect_git(str(worktree))
        assert status["success"] and "codex/docker" in status["stdout"]
        assert "+updated" in inspect_git(str(worktree), view="diff")["stdout"]
        mounts = json.loads(docker("inspect", session._container).stdout)[0]["Mounts"]
        assert all(mount["Destination"] != "/host-git" for mount in mounts)
        assert (source / "code.txt").read_text() == "original\n"
    finally:
        session.close()


def test_real_background_control_cancels_after_reply_and_releases_workspace(real_config, tmp_path, monkeypatch):
    import asyncio
    import time
    from research_agent import tools
    from research_agent.tools import ToolRegistry
    from research_agent.tools.builtin import _plugins
    from research_agent.tools.builtin import filesystem as fs
    from research_agent.execution import exclusive_request
    from research_agent.docker_ownership import scope_id
    from research_agent.server import cancel_execution
    registry = ToolRegistry()
    monkeypatch.setattr(tools, "get_registry", lambda: registry)
    monkeypatch.setattr(fs, "get_sandbox_config", lambda: real_config)
    registry.install_plugin(next(plugin for plugin in _plugins() if plugin.id == "shell"), apply_enabled=False)
    state = AgentState(workspace_dir=str(tmp_path), active_chat_id="background-real")
    state._cancellation = CancellationToken()
    state._execution_id = "background-real-execution"
    try:
        with exclusive_request(str(tmp_path), state.active_chat_id) as lease:
            state._request_lease = lease
            result = fs._handle_shell_exec({"command": "sleep 60", "background": True}, None, state, lambda *args: None)
            assert result.success
        # The reply/request is over; the job must still retain the workspace.
        with pytest.raises(RuntimeError):
            with exclusive_request(str(tmp_path), "another-window"):
                pass
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            running = docker("ps", "-q", "--filter", "label=io.research-agent.scope=" + scope_id())
            if running.stdout.strip():
                break
            time.sleep(0.05)
        else:
            pytest.fail("Background Docker command did not start")
        assert asyncio.run(cancel_execution(state._execution_id))["background_tasks"] == [result.data["task_id"]]
        registry.background_tasks.drain(timeout=15)
        assert registry.background_tasks.list(state.active_chat_id)[0]["status"] == "cancelled"
        assert not docker("ps", "-aq", "--filter", "label=io.research-agent.scope=" + scope_id()).stdout.strip()
        with exclusive_request(str(tmp_path), "another-window"):
            pass
    finally:
        registry.background_tasks.shutdown(timeout=15)
