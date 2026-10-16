import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from research_agent import docker_ownership as ownership
from research_agent import sandbox


def fake_engine(monkeypatch):
    containers, calls = {}, []

    def run(argv, **kwargs):
        calls.append(argv)
        args = argv[1:]
        if args[0] == "ps":
            filters = [args[i + 1][6:].split("=", 1) for i, arg in enumerate(args) if arg == "--filter"]
            ids = [key for key, info in containers.items()
                   if all(info["Config"]["Labels"].get(k) == v for k, v in filters)]
            return SimpleNamespace(returncode=0, stdout="\n".join(ids), stderr="")
        if args[0] == "inspect":
            return SimpleNamespace(returncode=0, stdout=json.dumps([containers[args[1]]]), stderr="")
        if args[0] == "rm":
            containers.pop(args[-1])
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(argv)

    monkeypatch.setattr(ownership.subprocess, "run", run)
    return containers, calls


def record(slot):
    return {"Name": "/" + slot.name, "Config": {"Labels": slot.labels}}


def test_orphan_recovery_skips_live_hosts_and_other_installations(monkeypatch):
    containers, _ = fake_engine(monkeypatch)
    cfg = sandbox.SandboxConfig(max_containers=2)
    alive = ownership.acquire_slot(cfg)
    dead = ownership.acquire_slot(cfg)
    containers["alive"] = record(alive)
    containers["dead"] = record(dead)
    containers["foreign"] = {"Name": "/unrelated", "Config": {"Labels": {}}}
    dead()
    try:
        assert ownership.reconcile_containers(cfg) == ["dead"]
        assert set(containers) == {"alive", "foreign"}
    finally:
        alive()


def test_recovery_includes_slots_after_capacity_is_reduced(monkeypatch):
    containers, _ = fake_engine(monkeypatch)
    cfg = sandbox.SandboxConfig(max_containers=2)
    first, second = ownership.acquire_slot(cfg), ownership.acquire_slot(cfg)
    containers["old"] = record(second)
    first()
    second()
    assert ownership.reconcile_containers(sandbox.SandboxConfig(max_containers=1)) == ["old"]


def test_wrong_name_is_never_removed(monkeypatch):
    containers, calls = fake_engine(monkeypatch)
    cfg = sandbox.SandboxConfig()
    slot = ownership.acquire_slot(cfg)
    info = record(slot)
    info["Name"] = "/someone-else"
    containers["wrong"] = info
    slot()
    with pytest.raises(RuntimeError, match="ownership mismatch"):
        ownership.reconcile_containers(cfg)
    assert not any(argv[1] == "rm" for argv in calls)


def test_new_admission_reconciles_under_slot_lock(monkeypatch, tmp_path):
    containers, _ = fake_engine(monkeypatch)
    cfg = sandbox.SandboxConfig(max_containers=1)
    slot = ownership.acquire_slot(cfg)
    containers["orphan"] = record(slot)
    expected = slot.name
    slot()
    owner = sandbox.own_oneshot(str(tmp_path), cfg, "ignored-legacy-name")
    try:
        assert not containers
        assert owner._container == expected
        with pytest.raises(RuntimeError, match="capacity"):
            ownership.acquire_slot(cfg)
    finally:
        owner._container = None
        owner.close()


def test_failed_recovery_refuses_container_start(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "docker_available", lambda *args: True)
    calls = []
    def fail(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=1, stdout="", stderr="daemon unavailable")
    monkeypatch.setattr(ownership.subprocess, "run", fail)
    session = sandbox.create_session(str(tmp_path), sandbox.SandboxConfig(backend="docker", max_containers=1))
    with pytest.raises(RuntimeError, match="reconciliation failed"):
        session.start()
    assert not any(argv[1] == "run" for argv in calls)
    release = ownership.acquire_slot(session.cfg)
    release()


def test_request_admission_fails_if_previous_resources_cannot_be_verified(monkeypatch):
    slot = ownership.acquire_slot(sandbox.SandboxConfig())
    slot.prepare(".")
    slot()
    monkeypatch.setattr(sandbox, "docker_available", lambda *args: False)
    with pytest.raises(RuntimeError, match="Cannot verify previous Docker resources"):
        ownership.reconcile_before_request()


def test_successful_cleanup_allows_file_requests_without_docker(monkeypatch, tmp_path):
    containers, _ = fake_engine(monkeypatch)
    owner = sandbox.own_oneshot(str(tmp_path), sandbox.SandboxConfig(), "")
    assert owner._slot_release.record_path.exists()
    owner._container = None  # confirmed --rm completion
    owner.close()
    monkeypatch.setattr(sandbox, "docker_available", lambda *args: pytest.fail("must remain lazy"))
    assert ownership.reconcile_before_request() == []


def test_process_crash_releases_os_lease_and_keeps_durable_intent(monkeypatch, tmp_path):
    native_run = subprocess.run
    containers, _ = fake_engine(monkeypatch)
    script = """
import os
from research_agent.docker_ownership import acquire_slot
from research_agent.sandbox import SandboxConfig
slot = acquire_slot(SandboxConfig(max_containers=1))
slot.prepare('.')
print(slot.name, flush=True)
os._exit(0)
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src") + os.pathsep + env.get("PYTHONPATH", "")
    child = native_run([sys.executable, "-c", script], env=env, capture_output=True,
                           text=True, timeout=15)
    assert child.returncode == 0, child.stderr
    slot = ownership.acquire_slot(sandbox.SandboxConfig(max_containers=1))
    try:
        assert slot.name == child.stdout.strip()
        assert slot.record_path.exists()
        containers["crashed"] = record(slot)
        assert slot.reconcile(sandbox.SandboxConfig()) == ["crashed"]
        assert not slot.record_path.exists()
    finally:
        slot()


def test_container_disappearing_during_inspection_is_already_clean(monkeypatch):
    def run(argv, **kwargs):
        if argv[1] == "ps":
            return SimpleNamespace(returncode=0, stdout="gone", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="No such object: gone")
    monkeypatch.setattr(ownership.subprocess, "run", run)
    assert ownership.reconcile_containers(sandbox.SandboxConfig()) == []


def test_failed_record_cleanup_retains_owner_and_capacity(monkeypatch, tmp_path):
    fake_engine(monkeypatch)
    cfg = sandbox.SandboxConfig(max_containers=1)
    owner = sandbox.own_oneshot(str(tmp_path), cfg, "")
    slot = owner._slot_release
    owner._container = None  # confirmed absent, but durable bookkeeping must finish
    try:
        with monkeypatch.context() as patch:
            def fail():
                raise OSError("record is temporarily locked")
            patch.setattr(slot, "forget", fail)
            with pytest.raises(OSError):
                owner.close()
            assert not owner._closed and slot.record_path.exists()
            assert sandbox._FAILED_CLEANUP[id(owner)] is owner
            with pytest.raises(RuntimeError, match="capacity"):
                ownership.acquire_slot(cfg)
    finally:
        owner.close()
    assert not slot.record_path.exists()


def test_slot_cleanup_uses_its_creation_data_directory(monkeypatch, tmp_path):
    cfg = sandbox.SandboxConfig()
    slot = ownership.acquire_slot(cfg)
    slot.prepare(str(tmp_path))
    original = slot.record_path
    replacement = tmp_path / "other-data"
    (replacement / "docker_slots").mkdir(parents=True)
    other_record = replacement / "docker_slots" / f"{slot.index}.json"
    other_record.write_text("must remain", encoding="utf-8")
    monkeypatch.setenv("RESEARCH_AGENT_DATA_DIR", str(replacement))
    try:
        slot.forget()
        assert not original.exists()
        assert other_record.read_text() == "must remain"
    finally:
        slot()
