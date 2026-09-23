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


def test_shell_exec_handler_routes_through_sandbox(monkeypatch, tmp_path):
    from research_agent.tools.builtin import filesystem as fs
    captured = {}

    def fake_run_command(command, workdir, timeout, cfg=None):
        captured.update(command=command, workdir=workdir, timeout=timeout)
        return {"success": True, "backend": "docker", "returncode": 0,
                "stdout": "ok", "stderr": "", "warning": None}

    monkeypatch.setattr(fs, "run_command", fake_run_command)
    state = types.SimpleNamespace(workspace_dir=str(tmp_path))
    events = []
    res = fs._handle_shell_exec({"command": "echo ok"}, None, state, lambda e, d: events.append(e))
    assert res.success and res.data["backend"] == "docker"
    assert captured["workdir"] == str(tmp_path)
    assert any(e == "tool" for e in events)
