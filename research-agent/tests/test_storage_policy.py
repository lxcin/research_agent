from pathlib import Path
from types import SimpleNamespace
import pytest
from click.testing import CliRunner

from research_agent.session_workspace import (prepare_session, merge_session, git, archive_session,
    restore_session, resolve_session, session_storage)
from research_agent.sandbox import SandboxConfig, SharedMount, build_session_argv, build_docker_argv


@pytest.fixture(autouse=True)
def empty_docker_slots(monkeypatch):
    # These tests exercise capacity/cleanup, not daemon discovery. Ownership
    # discovery has separate contract and real-engine tests.
    from research_agent.docker_ownership import DockerSlot
    monkeypatch.setattr(DockerSlot, "reconcile", lambda self, cfg: [])


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "code.txt").write_text("initial")
    git(root, "add", "code.txt")
    git(root, "commit", "-m", "initial")
    return root


def commit(binding, text):
    (Path(binding.workspace) / "code.txt").write_text(text)
    git(binding.workspace, "add", "code.txt")
    git(binding.workspace, "commit", "-m", "change")
    return git(binding.workspace, "rev-parse", "HEAD")


def merged_session(project, chat="archive"):
    binding = prepare_session(str(project), chat)
    base = git(project, "rev-parse", "HEAD")
    head = commit(binding, "merged work")
    merge_session(str(project), chat, expected_project_head=base, expected_session_head=head)
    return binding, head


def test_archive_preserves_branch_binding_and_restores_files(project):
    binding, head = merged_session(project)
    assert session_storage(str(project), "archive")["logical_bytes"] > 0
    archived = archive_session(str(project), "archive", expected_project_head=head, expected_session_head=head)
    assert archived.archived and not Path(binding.workspace).exists()
    assert git(project, "rev-parse", binding.branch) == head
    assert session_storage(str(project), "archive")["logical_bytes"] == 0
    with pytest.raises(RuntimeError, match="archived"):
        resolve_session(str(project), "archive")
    restored = restore_session(str(project), "archive")
    assert not restored.archived
    assert (Path(restored.workspace) / "code.txt").read_text() == "merged work"
    assert resolve_session(str(project), "archive").branch == binding.branch


@pytest.mark.parametrize("kind", ["modified", "untracked", "ignored"])
def test_archive_refuses_unpreserved_data(project, tmp_path, kind):
    binding, head = merged_session(project)
    if kind == "modified":
        path = Path(binding.workspace) / "code.txt"
    elif kind == "untracked":
        path = Path(binding.workspace) / "notes.txt"
    else:
        ignore = tmp_path / "ignore.txt"
        ignore.write_text("secret.env\n")
        git(project, "config", "core.excludesFile", str(ignore))
        path = Path(binding.workspace) / "secret.env"
    path.write_text("keep me")
    with pytest.raises(RuntimeError, match="ignored data"):
        archive_session(str(project), "archive", expected_project_head=head, expected_session_head=head)
    assert path.read_text() == "keep me"


def test_archive_requires_integrated_commit_and_idle_session(project):
    from research_agent.execution import exclusive_request
    binding = prepare_session(str(project), "active")
    base = git(project, "rev-parse", "HEAD")
    head = commit(binding, "not integrated")
    with pytest.raises(RuntimeError):
        archive_session(str(project), "active", expected_project_head=base, expected_session_head=head)
    assert Path(binding.workspace).exists()
    with exclusive_request(binding.workspace, "active"):
        with pytest.raises(RuntimeError, match="active request"):
            archive_session(str(project), "active", expected_project_head=base, expected_session_head=head)


def test_archive_cli_storage_and_restore(project):
    from research_agent.cli import main
    binding, head = merged_session(project)
    runner = CliRunner()
    assert runner.invoke(main, ["session-workspace", "storage", "--project", str(project), "--chat", "archive"]).exit_code == 0
    args = ["session-workspace", "archive", "--project", str(project), "--chat", "archive",
            "--expected-project-head", head, "--expected-session-head", head]
    result = runner.invoke(main, args)
    assert result.exit_code == 0, result.output
    info = runner.invoke(main, ["session-workspace", "info", "--project", str(project), "--chat", "archive"])
    assert info.exit_code == 0 and '"archived": true' in info.output
    restored = runner.invoke(main, ["session-workspace", "restore", "--project", str(project), "--chat", "archive"])
    assert restored.exit_code == 0 and Path(binding.workspace).exists()


def test_shared_data_mounts_are_read_only_and_apply_to_both_container_modes(tmp_path):
    cfg = SandboxConfig(shared_mounts=[SharedMount(str(tmp_path), "/shared/dataset")])
    for argv in (build_docker_argv("true", str(tmp_path), cfg), build_session_argv(str(tmp_path), cfg, "name")):
        mount = argv[argv.index("--mount") + 1]
        assert "target=/shared/dataset,readonly" in mount
    with pytest.raises(ValueError):
        SharedMount(str(tmp_path), "/work")
    with pytest.raises(ValueError):
        SharedMount(str(tmp_path), "/shared/../etc")
    with pytest.raises(ValueError):
        SandboxConfig(shared_mounts=[SharedMount(str(tmp_path), "/shared/a"), SharedMount(str(tmp_path), "/shared/a/nested")])


def test_container_slots_reject_over_capacity_and_release_on_close(monkeypatch, tmp_path):
    from research_agent import sandbox
    monkeypatch.setattr(sandbox, "docker_available", lambda *a: True)
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="container", stderr=""))
    cfg = SandboxConfig(backend="docker", max_containers=1)
    first = sandbox.create_session(str(tmp_path), cfg)
    second = sandbox.create_session(str(tmp_path), cfg)
    try:
        first.start()
        with pytest.raises(RuntimeError, match="capacity"):
            second.start()
        first.close()
        second.start()
    finally:
        first.close()
        second.close()


def test_failed_cleanup_keeps_capacity_until_retry(monkeypatch, tmp_path):
    from research_agent import sandbox
    monkeypatch.setattr(sandbox, "docker_available", lambda *a: True)
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="container", stderr=""))
    cfg = SandboxConfig(backend="docker", max_containers=1)
    first, second = sandbox.create_session(str(tmp_path), cfg), sandbox.create_session(str(tmp_path), cfg)
    first.start()
    try:
        monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
        with pytest.raises(RuntimeError):
            first.close()
        with pytest.raises(RuntimeError, match="capacity"):
            second.start()
    finally:
        monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
        first.close()
        second.close()


def test_one_shot_uses_same_capacity_pool(monkeypatch, tmp_path):
    from research_agent import sandbox, process_control
    from research_agent.execution import CancellationToken
    cfg = SandboxConfig(backend="docker", max_containers=1)
    owner = sandbox.own_oneshot(str(tmp_path), cfg, "held")
    monkeypatch.setattr(process_control, "run_local", lambda *a, **k: pytest.fail("must not start process"))
    monkeypatch.setattr(sandbox, "get_sandbox_config", lambda: cfg)
    try:
        with pytest.raises(RuntimeError, match="capacity"):
            process_control.run_docker("true", str(tmp_path), 10, CancellationToken())
        monkeypatch.setattr(sandbox, "resolve_backend", lambda cfg: ("docker", None))
        result = sandbox.run_command("true", str(tmp_path), 10, cfg)
        assert not result["success"] and "capacity" in result["error"]
    finally:
        owner._container = None
        owner.close()


def test_uncertain_oneshot_cleanup_quarantines_workspace(monkeypatch, tmp_path):
    from research_agent import sandbox, process_control
    from research_agent.execution import CancellationToken, RecoveryRequired, exclusive_request
    monkeypatch.setattr(sandbox, "get_sandbox_config", lambda: SandboxConfig(backend="docker", max_containers=1))
    def fail(*args, **kwargs):
        raise RuntimeError("transport failure")
    monkeypatch.setattr(process_control, "run_local", fail)
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    try:
        with pytest.raises(RecoveryRequired):
            process_control.run_docker("true", str(tmp_path), 10, CancellationToken())
        with pytest.raises(RuntimeError, match="quarantined"):
            with exclusive_request(str(tmp_path), "new"):
                pass
    finally:
        monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
        sandbox.close_all_sessions()


def test_confirmed_absent_container_releases_capacity(monkeypatch, tmp_path):
    from research_agent import sandbox
    cfg = SandboxConfig(backend="docker", max_containers=1)
    owner = sandbox.own_oneshot(str(tmp_path), cfg, "owned-missing")
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=1, stderr=f"Error response from daemon: No such container: {owner._container}"))
    try:
        owner.close()
        release = sandbox.acquire_docker_slot(cfg)
        release()
    finally:
        owner._container = None
        owner.close()
