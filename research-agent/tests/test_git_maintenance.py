"""Durability, coalescing, GC boundaries and the real detached worker."""
import os
from pathlib import Path
import subprocess
import time

import pytest

from research_agent import git_maintenance as maintenance


def git(ws, *args, data=None):
    result = subprocess.run(["git", *args], cwd=ws, input=data, capture_output=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.decode().strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "test")
    git(root, "config", "user.email", "test@example.com")
    (root / "code.txt").write_bytes(b"committed code")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "initial")
    return root


def due():
    filename = maintenance.database_path()
    with maintenance.database(filename) as db:
        db.execute("UPDATE jobs SET due=0")
    return filename


def test_foreground_only_persists_and_launches(repo, monkeypatch):
    launches = []
    monkeypatch.setattr(maintenance, "launch_worker", launches.append)
    monkeypatch.setattr(maintenance, "_gc", lambda job: pytest.fail("GC ran in foreground"))
    result = maintenance.enqueue(str(repo))
    assert result["success"] and launches == [maintenance.database_path()]
    row = maintenance.status()[0]
    assert row["status"] == "pending" and row["generation"] == 1
    assert row["due"] > time.time()


def test_worktrees_share_queue_and_debounce(repo, tmp_path):
    other = tmp_path / "other"
    git(repo, "worktree", "add", "-b", "other", str(other))
    assert maintenance.enqueue(str(repo))["success"]
    before = maintenance.status()[0]
    assert maintenance.enqueue(str(other))["success"]
    rows = maintenance.status()
    assert len(rows) == 1 and rows[0]["generation"] == 2
    assert rows[0]["due"] == before["due"]


def test_new_requests_during_gc_are_not_lost_and_cooldown_applies(repo, monkeypatch):
    maintenance.enqueue(str(repo))
    filename = due()
    def collect(job):
        assert maintenance.enqueue(str(repo))["success"]
    monkeypatch.setattr(maintenance, "_gc", collect)
    assert maintenance.process_due(filename)
    row = maintenance.status()[0]
    assert row["generation"] == 2 and row["completed"] == 1 and row["status"] == "pending"
    assert row["due"] >= row["last_success"] + maintenance.COOLDOWN
    assert not maintenance.process_due(filename)
    monkeypatch.setattr(maintenance, "_gc", lambda job: None)
    assert maintenance.process_due(filename, now=time.time() + maintenance.COOLDOWN + 1)
    assert maintenance.status()[0]["status"] == "idle"


def test_failed_launch_keeps_intent_and_resume_relaunches(repo, monkeypatch):
    def fail(filename):
        raise OSError("launch failed")
    monkeypatch.setattr(maintenance, "launch_worker", fail)
    assert maintenance.enqueue(str(repo))["success"]
    assert "launch failed" in maintenance.status()[0]["error"]
    launches = []
    monkeypatch.setattr(maintenance, "launch_worker", launches.append)
    maintenance.resume()
    assert launches == [maintenance.database_path()]


def test_failure_backoff_bounded_and_retry_does_not_lose_request(repo, monkeypatch):
    maintenance.enqueue(str(repo))
    filename = due()
    def fail(job):
        raise RuntimeError("Git is busy")
    monkeypatch.setattr(maintenance, "_gc", fail)
    for attempt in range(maintenance.MAX_ATTEMPTS):
        maintenance.process_due(filename, now=time.time() + 100000)
        row = maintenance.status()[0]
        assert row["attempts"] == attempt + 1 and "busy" in row["error"]
    assert row["status"] == "failed"
    assert maintenance.enqueue(str(repo))["success"]
    assert maintenance.status()[0]["attempts"] == 0


def test_repository_identity_change_blocks_execution(repo):
    maintenance.enqueue(str(repo))
    filename = due()
    with maintenance.database(filename) as db:
        db.execute("UPDATE jobs SET identity='[-1,-1]'")
    assert maintenance.process_due(filename)
    assert maintenance.status()[0]["status"] == "blocked"
    assert (repo / "code.txt").read_bytes() == b"committed code"


def test_external_gc_lock_is_preserved(repo):
    maintenance.enqueue(str(repo))
    filename = due()
    lock = repo / ".git" / "gc.pid"
    lock.write_bytes(b"external gc owner")
    assert maintenance.process_due(filename)
    assert maintenance.status()[0]["status"] == "pending"
    assert lock.read_bytes() == b"external gc owner"


def test_running_job_from_dead_worker_is_reclaimed(repo, monkeypatch):
    maintenance.enqueue(str(repo))
    filename = due()
    with maintenance.database(filename) as db:
        db.execute("UPDATE jobs SET status='running'")
    calls = []
    monkeypatch.setattr(maintenance, "_gc", calls.append)
    maintenance.work(filename)
    assert len(calls) == 1 and maintenance.status()[0]["status"] == "idle"


def test_second_worker_skips_live_lease(repo, monkeypatch):
    from research_agent.execution import exclusive_request
    maintenance.enqueue(str(repo))
    filename = due()
    monkeypatch.setattr(maintenance, "_gc", lambda job: pytest.fail("Second worker entered GC"))
    with exclusive_request(str(filename), "git-maintenance:" + str(filename)):
        maintenance.work(filename)
    assert maintenance.status()[0]["status"] == "pending"


def test_real_gc_prunes_aged_objects_preserves_recent_and_all_worktrees(repo, tmp_path, monkeypatch):
    other = tmp_path / "other"
    git(repo, "worktree", "add", "-b", "other", str(other))
    (other / "staged.txt").write_bytes(b"other window staged data")
    git(other, "add", "staged.txt")
    old = git(repo, "hash-object", "-w", "--stdin", data=b"old unreachable object")
    recent = git(repo, "hash-object", "-w", "--stdin", data=b"recent in-flight object")
    old_file = repo / ".git" / "objects" / old[:2] / old[2:]
    aged = time.time() - 30 * 86400
    os.utime(old_file, (aged, aged))
    git(repo, "config", "gc.pruneExpire", "now")
    git(repo, "config", "gc.worktreePruneExpire", "now")
    maintenance.enqueue(str(repo))
    with monkeypatch.context() as patch:
        patch.setenv("GIT_OBJECT_DIRECTORY", str(tmp_path / "not-the-queued-object-store"))
        patch.setenv("GIT_INDEX_FILE", str(tmp_path / "not-the-worktree-index"))
        patch.setenv("GIT_NAMESPACE", "not-the-queued-namespace")
        assert maintenance.process_due(due())
    row = maintenance.status()[0]
    assert row["status"] == "idle", row
    assert subprocess.run(["git", "cat-file", "-e", old], cwd=repo, capture_output=True).returncode != 0
    assert git(repo, "cat-file", "-p", recent) == "recent in-flight object"
    assert git(other, "show", ":staged.txt") == "other window staged data"
    assert (other / "staged.txt").exists()
    assert (repo / "code.txt").read_bytes() == b"committed code"


def test_real_detached_worker_finishes_without_host_waiting_for_gc(repo):
    maintenance.enqueue(str(repo))
    filename = due()
    process = maintenance._launch_detached(filename)
    try:
        assert process.wait(timeout=30) == 0
        row = maintenance.status()[0]
        assert row["status"] == "idle" and row["completed"] == row["generation"], row
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def test_checkpoint_retirement_schedules_gc_but_first_turn_does_not(repo, monkeypatch):
    from types import SimpleNamespace
    from research_agent import checkpoint
    calls = []
    monkeypatch.setattr(maintenance, "enqueue", calls.append)
    state = SimpleNamespace()
    first = checkpoint.snapshot_for_run(state, str(repo))
    assert calls == []
    assert checkpoint.seal_checkpoint(str(repo), first)["success"]
    checkpoint.snapshot_for_run(state, str(repo))
    assert calls == [str(repo)]


def test_gc_does_not_expire_reflogs_even_if_repository_config_requests_it(repo):
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    shadow = git(repo, "commit-tree", tree, "-m", "reflog-only commit")
    head = git(repo, "rev-parse", "HEAD")
    env = dict(os.environ, GIT_COMMITTER_DATE=f"{int(time.time()) - 30 * 86400} +0000")
    for oid in (shadow, head):
        result = subprocess.run(["git", "update-ref", "--create-reflog", "refs/heads/temporary", oid],
                                cwd=repo, env=env, capture_output=True)
        assert result.returncode == 0, result.stderr
    before = git(repo, "reflog", "show", "--format=%H", "temporary")
    assert shadow in before
    git(repo, "config", "gc.reflogExpire", "now")
    git(repo, "config", "gc.reflogExpireUnreachable", "now")
    git(repo, "config", "gc.refs/heads/*.reflogExpire", "now")
    maintenance.enqueue(str(repo))
    maintenance.process_due(due())
    assert maintenance.status()[0]["status"] == "idle"
    assert git(repo, "reflog", "show", "--format=%H", "temporary") == before
    assert git(repo, "cat-file", "-t", shadow) == "commit"


def test_cli_request_and_status_use_durable_queue(repo):
    import json
    from click.testing import CliRunner
    from research_agent.cli import main
    runner = CliRunner()
    result = runner.invoke(main, ["git-maintenance", "request", "--workspace", str(repo)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["success"]
    result = runner.invoke(main, ["git-maintenance", "status"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["status"] == "pending"


def test_gc_timeout_is_recorded_for_background_retry(repo, monkeypatch):
    maintenance.enqueue(str(repo))
    filename = due()
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 300)
    monkeypatch.setattr(maintenance.subprocess, "run", timeout)
    assert maintenance.process_due(filename)
    row = maintenance.status()[0]
    assert row["status"] == "pending" and row["attempts"] == 1
    assert "timed out" in row["error"]


def test_worker_hard_exit_leaves_recoverable_job_and_releases_os_lease(repo):
    import sys
    maintenance.enqueue(str(repo))
    filename = due()
    script = """
import os, sys
from research_agent import git_maintenance as maintenance
maintenance._gc = lambda job: os._exit(17)
maintenance.work(sys.argv[1])
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(maintenance.__file__).resolve().parents[1]) + os.pathsep + env.get("PYTHONPATH", "")
    child = subprocess.run([sys.executable, "-c", script, str(filename)], env=env, capture_output=True, timeout=30)
    assert child.returncode == 17, child.stderr
    assert maintenance.status()[0]["status"] == "running"
    maintenance.work(filename)
    assert maintenance.status()[0]["status"] == "idle"


def test_enqueue_during_worker_exit_has_available_lease_for_relaunch(repo, monkeypatch):
    from threading import Thread
    from research_agent.execution import RequestLease, exclusive_request
    maintenance.enqueue(str(repo))
    filename = due()
    monkeypatch.setattr(maintenance, "_gc", lambda job: None)
    original = RequestLease.release
    threads, launches = [], []
    armed = True
    def launch(filename):
        with exclusive_request(str(filename), "git-maintenance:" + str(filename)):
            launches.append(filename)
    monkeypatch.setattr(maintenance, "launch_worker", launch)
    def release(lease):
        nonlocal armed
        original(lease)
        if armed:
            armed = False
            thread = Thread(target=lambda: maintenance.enqueue(str(repo)))
            threads.append(thread)
            thread.start()
    monkeypatch.setattr(RequestLease, "release", release)
    maintenance.work(filename)
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert launches == [filename]
    row = maintenance.status()[0]
    assert row["status"] == "pending" and row["generation"] == 2 and row["completed"] == 1


def test_enqueue_ignores_inherited_repository_and_object_overrides(repo, tmp_path, monkeypatch):
    other = tmp_path / "inherited-repository"
    other.mkdir()
    git(other, "init", "-q")
    with monkeypatch.context() as patch:
        patch.setenv("GIT_DIR", str(other / ".git"))
        patch.setenv("GIT_COMMON_DIR", str(other / ".git"))
        patch.setenv("GIT_WORK_TREE", str(other))
        patch.setenv("GIT_INDEX_FILE", str(other / "other-index"))
        patch.setenv("GIT_OBJECT_DIRECTORY", str(other / ".git" / "objects"))
        patch.setenv("GIT_NAMESPACE", "inherited")
        assert maintenance.enqueue(str(repo))["success"]
    row = maintenance.status()[0]
    assert row["common_dir"] == os.path.normcase(os.path.realpath(repo / ".git"))
    assert maintenance.process_due(due())
    assert maintenance.status()[0]["status"] == "idle"
    assert not (other / "other-index").exists()


def test_host_environment_drops_git_config_overrides(monkeypatch):
    from research_agent.git_environment import host_git_environment
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.worktree")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "other")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "inherited")
    env = host_git_environment()
    assert not any(key in env for key in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0", "GIT_CONFIG_PARAMETERS"))
