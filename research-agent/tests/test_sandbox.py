# tests/test_sandbox.py — sandboxed shell execution backend selection + argv
import os
import types

import pytest

from research_agent import sandbox


def test_build_docker_argv_defaults_are_hardened(tmp_path):
    cfg = sandbox.SandboxConfig()
    argv = sandbox.build_docker_argv("echo hi", str(tmp_path), cfg)
    joined = " ".join(argv)
    assert argv[0] == "docker" and "run" in argv and "--rm" in argv
    assert "--network" in argv and "none" in argv          # network off by default
    assert "-v" in argv and f"{str(tmp_path).replace(chr(92), '/')}:{sandbox.MOUNT_TARGET}" in joined
    assert "-w" in argv and sandbox.MOUNT_TARGET in argv
    assert "--memory" in argv and "--cpus" in argv and "--pids-limit" in argv
    assert "no-new-privileges" in argv
    assert "--read-only" in argv and "--tmpfs" in argv
    assert argv[-3:] == ["sh", "-lc", "echo hi"]


def test_build_docker_argv_network_enabled(tmp_path):
    cfg = sandbox.SandboxConfig(network=True)
    argv = sandbox.build_docker_argv("curl x", str(tmp_path), cfg)
    i = argv.index("--network")
    assert argv[i + 1] == "bridge"


def test_local_run_command_uses_replace_decoding(monkeypatch, tmp_path):
    captured = {}

    class _Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(*a, **k):
        captured.update(k)
        return _Completed()

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    cfg = sandbox.SandboxConfig(backend="local")
    r = sandbox.run_command("echo ok", str(tmp_path), 5, cfg)
    assert r["success"] is True
    assert captured.get("errors") == "replace"   # no UnicodeDecodeError on mixed output
    assert captured.get("text") is True


def test_resolve_backend_explicit_docker_unavailable(monkeypatch):
    monkeypatch.setattr(sandbox, "docker_available", lambda *a, **k: False)
    backend, warning = sandbox.resolve_backend(sandbox.SandboxConfig(backend="docker"))
    assert backend == "docker" and warning  # no silent local fallback


def test_resolve_backend_auto_falls_back_to_local(monkeypatch):
    monkeypatch.setattr(sandbox, "docker_available", lambda *a, **k: False)
    backend, warning = sandbox.resolve_backend(sandbox.SandboxConfig(backend="auto"))
    assert backend == "local" and warning


def test_resolve_backend_auto_uses_docker(monkeypatch):
    monkeypatch.setattr(sandbox, "docker_available", lambda *a, **k: True)
    backend, warning = sandbox.resolve_backend(sandbox.SandboxConfig(backend="auto"))
    assert backend == "docker" and warning is None


def test_resolve_backend_local_never_probes(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("docker_available should not be called for local")
    monkeypatch.setattr(sandbox, "docker_available", boom)
    assert sandbox.resolve_backend(sandbox.SandboxConfig(backend="local")) == ("local", None)


def test_run_command_local_executes_in_workdir(tmp_path):
    cfg = sandbox.SandboxConfig(backend="local")
    res = sandbox.run_command("echo hi", str(tmp_path), timeout=10, cfg=cfg)
    assert res["success"] and res["backend"] == "local"
    assert "hi" in res["stdout"]


def test_run_command_forced_docker_unavailable_fails_not_local(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "docker_available", lambda *a, **k: False)
    cfg = sandbox.SandboxConfig(backend="docker")
    res = sandbox.run_command("echo should-not-run", str(tmp_path), timeout=10, cfg=cfg)
    assert res["success"] is False
    assert res["backend"] == "docker" and res["returncode"] == -1
    assert "echo should-not-run" not in (res.get("stdout") or "")


def test_run_command_timeout_reported(tmp_path):
    cfg = sandbox.SandboxConfig(backend="local")
    res = sandbox.run_command("sleep 5", str(tmp_path),
                              timeout=1 if os.name != "nt" else 2, cfg=cfg)
    if os.name == "nt":
        pytest.skip("timeout semantics differ on Windows shell")
    assert res["success"] is False and res.get("error") == "timeout"


def test_shell_exec_handler_routes_through_session(monkeypatch, tmp_path):
    from research_agent.tools.builtin import filesystem as fs
    captured = {}

    class FakeSession:
        backend = "docker"

        def exec(self, command, timeout):
            captured.update(command=command, timeout=timeout)
            return {"success": True, "backend": "docker", "returncode": 0,
                    "stdout": "ok", "stderr": "", "warning": None}

    monkeypatch.setattr(fs, "get_session",
                        lambda state, workdir, cfg=None: FakeSession())
    state = types.SimpleNamespace(workspace_dir=str(tmp_path), caps={})
    events = []
    res = fs._handle_shell_exec({"command": "echo ok"}, None, state, lambda e, d: events.append(e))
    assert res.success and res.data["backend"] == "docker"
    assert captured["command"] == "echo ok"
    assert any(e == "tool" for e in events)


# ── Session lifecycle (session-scoped, not per-command) ──

class _Completed:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _docker_fake(calls):
    def fake_run(argv, *a, **k):
        calls.append(argv)
        if len(argv) > 1 and argv[1] == "run":
            return _Completed(0, "cid")
        if len(argv) > 1 and argv[1] == "exec":
            return _Completed(0, "ok")
        if len(argv) > 1 and argv[1] == "inspect":
            return _Completed(0, "true")
        return _Completed(0, "")
    return fake_run


def test_session_reuses_one_container_for_many_commands(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(sandbox, "docker_available", lambda *a, **k: True)
    monkeypatch.setattr(sandbox.subprocess, "run", _docker_fake(calls))
    cfg = sandbox.SandboxConfig(backend="docker", session="ephemeral")
    sess = sandbox.create_session(str(tmp_path), cfg)
    assert sess.exec("echo a", 5)["success"]
    assert sess.exec("echo b", 5)["success"]
    runs = [c for c in calls if len(c) > 1 and c[1] == "run"]
    execs = [c for c in calls if len(c) > 1 and c[1] == "exec"]
    assert len(runs) == 1        # container started once
    assert len(execs) == 2       # both commands via docker exec
    sess.close()


def test_get_session_reuses_within_run_and_end_run_closes(monkeypatch, tmp_path):
    from research_agent.models import AgentState
    calls = []
    monkeypatch.setattr(sandbox, "docker_available", lambda *a, **k: True)
    monkeypatch.setattr(sandbox.subprocess, "run", _docker_fake(calls))
    state = AgentState(user_input="t")
    state.workspace_dir = str(tmp_path)
    cfg = sandbox.SandboxConfig(backend="docker", session="ephemeral")
    s1 = sandbox.get_session(state, str(tmp_path), cfg)
    s2 = sandbox.get_session(state, str(tmp_path), cfg)
    assert s1 is s2                              # reused within the run
    s1.exec("echo hi", 5)
    sandbox.end_run(state)
    assert any(len(c) > 1 and c[1] == "rm" for c in calls)  # container torn down


def test_persistent_session_is_rejected():
    import pytest
    with pytest.raises(ValueError, match="Only ephemeral"):
        sandbox.SandboxConfig(session="persistent")


def test_legacy_config_migrates_to_run_scoped_session(monkeypatch, caplog):
    from research_agent import config
    monkeypatch.setattr(config, "load_config", lambda: {
        "shell": {"session": "persistent", "idle_timeout": 1800}})
    assert sandbox.get_sandbox_config().session == "ephemeral"
    assert "retired" in caplog.text


def test_ephemeral_session_not_shared_across_runs(monkeypatch, tmp_path):
    from research_agent.models import AgentState
    monkeypatch.setattr(sandbox, "docker_available", lambda *a, **k: True)
    monkeypatch.setattr(sandbox.subprocess, "run", _docker_fake([]))
    cfg = sandbox.SandboxConfig(backend="docker", session="ephemeral")
    st1 = AgentState(user_input="t")
    st1.workspace_dir = str(tmp_path)
    st2 = AgentState(user_input="t")
    st2.workspace_dir = str(tmp_path)
    a = sandbox.get_session(st1, str(tmp_path), cfg)
    sandbox.end_run(st1)
    b = sandbox.get_session(st2, str(tmp_path), cfg)
    assert a is not b                            # fresh session per run
    sandbox.end_run(st2)


def test_session_start_fails_when_docker_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "docker_available", lambda *a, **k: False)
    cfg = sandbox.SandboxConfig(backend="docker")
    sess = sandbox.create_session(str(tmp_path), cfg)
    res = sess.exec("echo x", 5)
    assert res["success"] is False and res["backend"] == "docker"
    assert "docker" in (res.get("error") or "").lower()


def test_direct_docker_exec_timeout_stops_container_before_reuse(monkeypatch, tmp_path):
    import subprocess
    calls = []
    fake = _docker_fake(calls)
    def run(argv, **kwargs):
        if argv[1] == "exec":
            raise subprocess.TimeoutExpired(argv, 1)
        return fake(argv, **kwargs)
    monkeypatch.setattr(sandbox, "docker_available", lambda *args: True)
    monkeypatch.setattr(sandbox.subprocess, "run", run)
    session = sandbox.create_session(str(tmp_path), sandbox.SandboxConfig(backend="docker"))
    result = session.exec("sleep 60", 1)
    assert result["error"] == "timeout" and session._closed
    assert any(argv[1] == "rm" for argv in calls)
    assert session._slot_release is None
