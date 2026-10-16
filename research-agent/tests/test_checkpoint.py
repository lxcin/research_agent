# tests/test_checkpoint.py — workspace snapshot + rollback (git-based)
import os
import shutil
import subprocess
import types

import pytest

from research_agent import checkpoint

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(not GIT, reason="git not available")


def _git(ws, *args):
    return subprocess.run(["git", *args], cwd=ws, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.name", "t")
    _git(ws, "config", "user.email", "t@t")
    (tmp_path / "ws" / "a.txt").write_text("v1", encoding="utf-8")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "init")
    return ws


def test_create_checkpoint_is_noop_for_non_repo(tmp_path):
    plain = str(tmp_path / "plain")
    os.makedirs(plain)
    assert checkpoint.create_checkpoint(plain)["success"] is False
    assert checkpoint.list_checkpoints(plain) == []
    assert checkpoint.restore_checkpoint(plain, "x")["success"] is False


def test_checkpoint_then_restore_reverts_pollution(repo):
    # checkpoint at v1 with an untracked file present
    with open(os.path.join(repo, "untracked.txt"), "w", encoding="utf-8") as f:
        f.write("preexisting")

    cp = checkpoint.create_checkpoint(repo, label="before-cmd")
    assert cp["success"] and cp["ref"].startswith(checkpoint.REF_PREFIX)

    # "command" pollutes the workspace: modifies tracked, deletes, adds new
    with open(os.path.join(repo, "a.txt"), "w", encoding="utf-8") as f:
        f.write("CORRUPTED")
    with open(os.path.join(repo, "new.txt"), "w", encoding="utf-8") as f:
        f.write("junk")
    os.remove(os.path.join(repo, "untracked.txt"))

    assert checkpoint.seal_checkpoint(repo, cp["ref"])["success"]
    review = checkpoint.review_checkpoint(repo, cp["ref"])
    assert review["success"], review
    res = checkpoint.restore_checkpoint(repo, cp["ref"], review_token=review["review_token"])
    assert res["success"]
    assert open(os.path.join(repo, "a.txt"), encoding="utf-8").read() == "v1"
    assert os.path.exists(os.path.join(repo, "untracked.txt"))
    assert not os.path.exists(os.path.join(repo, "new.txt"))


def test_checkpoint_preserves_uncommitted_agent_edits(repo):
    # agent edits on top of HEAD, not yet kept
    with open(os.path.join(repo, "a.txt"), "w", encoding="utf-8") as f:
        f.write("agent-edit")
    cp = checkpoint.create_checkpoint(repo)
    assert cp["success"]
    # command overwrites the agent edit
    with open(os.path.join(repo, "a.txt"), "w", encoding="utf-8") as f:
        f.write("clobbered")
    assert checkpoint.seal_checkpoint(repo, cp["ref"])["success"]
    review = checkpoint.review_checkpoint(repo, cp["ref"])
    assert review["success"], review
    assert checkpoint.restore_checkpoint(repo, cp["ref"], review_token=review["review_token"])["success"]
    # rollback restores the AGENT-EDIT state, not HEAD ("v1")
    assert open(os.path.join(repo, "a.txt"), encoding="utf-8").read() == "agent-edit"


def test_list_and_unknown_restore(repo):
    assert checkpoint.create_checkpoint(repo)["success"]
    refs = checkpoint.list_checkpoints(repo)
    assert refs and refs[0]["ref"].startswith(checkpoint.REF_PREFIX)
    assert checkpoint.restore_checkpoint(repo, "refs/research-agent/checkpoints/nope")["success"] is False


def test_checkpoint_rejects_human_edit_after_agent_baseline(repo):
    cp = checkpoint.create_checkpoint(repo)
    path = os.path.join(repo, "a.txt")
    with open(path, "w") as stream:
        stream.write("agent output")
    checkpoint.seal_checkpoint(repo, cp["ref"])
    with open(path, "w") as stream:
        stream.write("human correction")
    result = checkpoint.restore_checkpoint(repo, cp["ref"])
    assert not result["success"]
    assert "a.txt" in result["conflicts"]
    with open(path) as stream:
        assert stream.read() == "human correction"


def test_checkpoint_without_review_baseline_fails_closed(repo):
    cp = checkpoint.create_checkpoint(repo)
    assert not checkpoint.restore_checkpoint(repo, cp["ref"])["success"]


# ── Granularity: turn | command | off ──

def test_get_checkpoint_mode_normalization(monkeypatch):
    import research_agent.config as cfgmod
    cases = [(True, "turn"), (False, "off"), ("command", "turn"),
             ("off", "off"), ("turn", "turn"), ("bogus", "turn"), (None, "turn")]
    for raw, expected in cases:
        monkeypatch.setattr(cfgmod, "load_config",
                            lambda _r=raw: {"shell": {"checkpoint": _r}})
        assert checkpoint.get_checkpoint_mode() == expected


def test_create_checkpoint_dedupes_unchanged_workspace(repo):
    a = checkpoint.create_checkpoint(repo, label="a")
    b = checkpoint.create_checkpoint(repo, label="b")
    assert a["success"] and b["success"]
    assert b.get("skipped") is True
    assert b["ref"] == a["ref"]


def test_create_checkpoint_no_dedupe(repo):
    a = checkpoint.create_checkpoint(repo, label="a")
    b = checkpoint.create_checkpoint(repo, label="b", dedupe=False)
    assert b.get("skipped") is None
    assert b["ref"] != a["ref"]


def test_snapshot_for_run_turn_creates_and_stores(repo, monkeypatch):
    import research_agent.config as cfgmod
    monkeypatch.setattr(cfgmod, "load_config",
                        lambda: {"shell": {"checkpoint": "turn"}})
    st = types.SimpleNamespace()
    ref = checkpoint.snapshot_for_run(st, repo, label="run")
    assert ref.startswith(checkpoint.REF_PREFIX)
    assert st._last_checkpoint == ref


def test_snapshot_for_run_off_is_noop(repo, monkeypatch):
    import research_agent.config as cfgmod
    monkeypatch.setattr(cfgmod, "load_config",
                        lambda: {"shell": {"checkpoint": "off"}})
    st = types.SimpleNamespace()
    assert checkpoint.snapshot_for_run(st, repo) == ""


def test_maybe_checkpoint_turn_reports_run_ref(repo, monkeypatch):
    from research_agent.tools.builtin import filesystem as fs
    import research_agent.config as cfgmod
    monkeypatch.setattr(cfgmod, "load_config",
                        lambda: {"shell": {"checkpoint": "turn"}})
    st = types.SimpleNamespace(_last_checkpoint="refs/research-agent/checkpoints/run-x")
    # turn mode must NOT create a per-command checkpoint; it reports the run ref
    assert fs._maybe_checkpoint(st, repo) == "refs/research-agent/checkpoints/run-x"
    assert len(checkpoint.list_checkpoints(repo)) == 0


def test_legacy_command_mode_reports_turn_without_extra_snapshot(repo, monkeypatch):
    from research_agent.tools.builtin import filesystem as fs
    import research_agent.config as cfgmod
    monkeypatch.setattr(cfgmod, "load_config",
                        lambda: {"shell": {"checkpoint": "command"}})
    st = types.SimpleNamespace(_last_checkpoint="existing-turn")
    ref = fs._maybe_checkpoint(st, repo)
    assert ref == "existing-turn"
    assert checkpoint.list_checkpoints(repo) == []


def test_maybe_checkpoint_off_is_empty(repo, monkeypatch):
    from research_agent.tools.builtin import filesystem as fs
    import research_agent.config as cfgmod
    monkeypatch.setattr(cfgmod, "load_config",
                        lambda: {"shell": {"checkpoint": "off"}})
    st = types.SimpleNamespace()
    assert fs._maybe_checkpoint(st, repo) == ""


def test_rollback_preserves_preexisting_staging_and_removes_agent_staged_additions(repo):
    from pathlib import Path
    path = Path(repo) / "a.txt"
    path.write_text("human staged\n", encoding="utf-8")
    assert _git(repo, "add", "a.txt").returncode == 0
    original_index = checkpoint._index_state(repo)["data"]
    path.write_text("human working\n", encoding="utf-8")
    (Path(repo) / "human-untracked.txt").write_text("keep\n", encoding="utf-8")
    cp = checkpoint.create_checkpoint(repo)
    path.write_text("agent changes\n", encoding="utf-8")
    (Path(repo) / "agent-added.txt").write_text("agent\n", encoding="utf-8")
    assert _git(repo, "add", "-A").returncode == 0
    assert checkpoint.seal_checkpoint(repo, cp["ref"])["success"]
    review = checkpoint.review_checkpoint(repo, cp["ref"])
    assert review["success"] and review["restore_index"], review
    result = checkpoint.restore_checkpoint(repo, cp["ref"], review_token=review["review_token"])
    assert result["success"], result
    assert path.read_text() == "human working\n"
    assert not (Path(repo) / "agent-added.txt").exists()
    assert (Path(repo) / "human-untracked.txt").read_text() == "keep\n"
    assert checkpoint._index_state(repo)["data"] == original_index
    assert _git(repo, "ls-files", "human-untracked.txt").stdout == ""


def test_checkpoint_restore_requires_review_token(repo):
    cp = checkpoint.create_checkpoint(repo)
    assert checkpoint.seal_checkpoint(repo, cp["ref"])["success"]
    assert not checkpoint.restore_checkpoint(repo, cp["ref"])["success"]
    assert not checkpoint.restore_checkpoint(repo, cp["ref"], review_token="forged")["success"]


def test_sealed_baseline_is_immutable_and_new_round_does_not_reuse_it(repo):
    from pathlib import Path
    cp = checkpoint.create_checkpoint(repo)
    assert checkpoint.seal_checkpoint(repo, cp["ref"])["success"]
    new = checkpoint.create_checkpoint(repo)
    assert new["ref"] != cp["ref"]
    (Path(repo) / "a.txt").write_text("human edit", encoding="utf-8")
    assert not checkpoint.seal_checkpoint(repo, cp["ref"])["success"]
    assert not checkpoint.review_checkpoint(repo, cp["ref"])["success"]


def test_human_staging_after_review_is_rejected(repo):
    from pathlib import Path
    cp = checkpoint.create_checkpoint(repo)
    (Path(repo) / "a.txt").write_text("agent\n", encoding="utf-8")
    checkpoint.seal_checkpoint(repo, cp["ref"])
    review = checkpoint.review_checkpoint(repo, cp["ref"])
    _git(repo, "add", "a.txt")
    index = checkpoint._index_state(repo)["data"]
    result = checkpoint.restore_checkpoint(repo, cp["ref"], review_token=review["review_token"])
    assert not result["success"] and result["index_conflict"]
    assert checkpoint._index_state(repo)["data"] == index
    assert (Path(repo) / "a.txt").read_text() == "agent\n"


def test_checkpoints_are_scoped_to_the_physical_worktree(repo, tmp_path):
    from pathlib import Path
    other = tmp_path / "other"
    assert _git(repo, "worktree", "add", "-b", "codex/other", str(other)).returncode == 0
    cp = checkpoint.create_checkpoint(str(other))
    assert cp["success"]
    assert checkpoint.list_checkpoints(repo) == []
    assert not checkpoint.review_checkpoint(repo, cp["ref"])["success"]
    assert not checkpoint.review_checkpoint(repo, cp["sha"])["success"]


def test_index_lock_owned_by_another_writer_is_never_removed(repo):
    from pathlib import Path
    cp = checkpoint.create_checkpoint(repo)
    checkpoint.seal_checkpoint(repo, cp["ref"])
    lock = Path(checkpoint._index_state(repo)["path"] + ".lock")
    lock.write_bytes(b"other git operation")
    try:
        assert not checkpoint.review_checkpoint(repo, cp["ref"])["success"]
        assert not checkpoint.restore_checkpoint(repo, cp["ref"], review_token="old")["success"]
        assert lock.read_bytes() == b"other git operation"
    finally:
        lock.unlink()


def test_interrupted_rollback_resumes_only_after_a_new_review(repo, monkeypatch):
    from pathlib import Path
    (Path(repo) / "b.txt").write_text("before-b", encoding="utf-8")
    cp = checkpoint.create_checkpoint(repo)
    (Path(repo) / "a.txt").write_text("agent-a", encoding="utf-8")
    (Path(repo) / "b.txt").write_text("agent-b", encoding="utf-8")
    checkpoint.seal_checkpoint(repo, cp["ref"])
    review = checkpoint.review_checkpoint(repo, cp["ref"])
    original = os.replace
    def fail(src, dest):
        if Path(dest).name == "b.txt":
            raise OSError("interrupted replacement")
        return original(src, dest)
    with monkeypatch.context() as patch:
        patch.setattr(checkpoint.os, "replace", fail)
        result = checkpoint.restore_checkpoint(repo, cp["ref"], review_token=review["review_token"])
    assert not result["success"] and result["partial"] and result["backup"]
    refreshed = checkpoint.review_checkpoint(repo, cp["ref"])
    assert refreshed["success"] and refreshed["resume"]
    assert refreshed["review_token"] != review["review_token"]
    assert not checkpoint.restore_checkpoint(repo, cp["ref"], review_token=review["review_token"])["success"]
    assert checkpoint.restore_checkpoint(repo, cp["ref"], review_token=refreshed["review_token"])["success"]
    assert (Path(repo) / "a.txt").read_text() == "v1"
    assert (Path(repo) / "b.txt").read_text() == "before-b"


def test_process_crash_preserves_and_recovers_owned_index_lock(repo):
    from pathlib import Path
    import sys
    (Path(repo) / "b.txt").write_text("before-b", encoding="utf-8")
    cp = checkpoint.create_checkpoint(repo)
    (Path(repo) / "a.txt").write_text("agent-a", encoding="utf-8")
    (Path(repo) / "b.txt").write_text("agent-b", encoding="utf-8")
    checkpoint.seal_checkpoint(repo, cp["ref"])
    review = checkpoint.review_checkpoint(repo, cp["ref"])
    script = """
import os, sys
from pathlib import Path
from research_agent import checkpoint
original = os.replace
def crash(src, dest):
    original(src, dest)
    if Path(dest).name == 'a.txt':
        os._exit(17)
checkpoint.os.replace = crash
checkpoint.restore_checkpoint(sys.argv[1], sys.argv[2], review_token=sys.argv[3])
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, "-c", script, repo, cp["ref"], review["review_token"]],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 17, result.stderr
    lock = Path(checkpoint._index_state(repo)["path"] + ".lock")
    assert lock.exists()
    refreshed = checkpoint.review_checkpoint(repo, cp["ref"])
    assert refreshed["success"] and refreshed["recover_owned_index_lock"], refreshed
    restored = checkpoint.restore_checkpoint(repo, cp["ref"], review_token=refreshed["review_token"])
    assert restored["success"], restored
    assert not lock.exists()
    assert (Path(repo) / "a.txt").read_text() == "v1"
    assert (Path(repo) / "b.txt").read_text() == "before-b"


def test_off_mode_does_not_reuse_a_previous_round_checkpoint(repo, monkeypatch):
    from research_agent import config
    state = types.SimpleNamespace(_last_checkpoint="previous-round")
    monkeypatch.setattr(config, "load_config", lambda: {"shell": {"checkpoint": "off"}})
    assert checkpoint.snapshot_for_run(state, repo) == ""
    assert state._last_checkpoint == ""


def test_checkpoint_publication_failure_is_not_success(repo, monkeypatch):
    original = checkpoint._run
    def fail(args, *other, **kwargs):
        if args[0] == "update-ref":
            return types.SimpleNamespace(returncode=1, stdout="", stderr="publication failed")
        return original(args, *other, **kwargs)
    monkeypatch.setattr(checkpoint, "_run", fail)
    assert not checkpoint.create_checkpoint(repo)["success"]


def test_checkpoint_cli_review_and_restore(repo):
    from pathlib import Path
    import json
    from click.testing import CliRunner
    from research_agent.cli import main
    cp = checkpoint.create_checkpoint(repo)
    (Path(repo) / "a.txt").write_text("agent", encoding="utf-8")
    checkpoint.seal_checkpoint(repo, cp["ref"])
    runner = CliRunner()
    reviewed = runner.invoke(main, ["checkpoint", "review", "--workspace", repo, "--ref", cp["ref"]])
    assert reviewed.exit_code == 0, reviewed.output
    token = json.loads(reviewed.output)["review_token"]
    restored = runner.invoke(main, ["checkpoint", "restore", "--workspace", repo, "--ref", cp["ref"], "--review-token", token])
    assert restored.exit_code == 0, restored.output
    assert (Path(repo) / "a.txt").read_text() == "v1"


def test_ignored_human_file_at_restore_target_is_preserved(repo):
    from pathlib import Path
    target = Path(repo) / "preserved.txt"
    target.write_text("before\n", encoding="utf-8")
    cp = checkpoint.create_checkpoint(repo)
    target.unlink()
    exclude = Path(repo) / ".git" / "info" / "exclude"
    with exclude.open("a", encoding="utf-8") as stream:
        stream.write("\npreserved.txt\n")
    assert checkpoint.seal_checkpoint(repo, cp["ref"])["success"]
    target.write_text("human private content\n", encoding="utf-8")
    review = checkpoint.review_checkpoint(repo, cp["ref"])
    assert not review["success"] and review["conflicts"] == ["preserved.txt"]
    assert target.read_text() == "human private content\n"


@pytest.mark.parametrize("uncertain", [False, True])
def test_cancelled_agent_can_review_rollback_but_unknown_effects_cannot(repo, monkeypatch, uncertain):
    from research_agent.agent import run_agent
    from research_agent.models import AgentState
    from research_agent.tools import ToolRegistry
    from research_agent.kernel import AgentKernel, ToolCall
    from research_agent.execution import Cancelled, RecoveryRequired
    monkeypatch.setattr(ToolRegistry, "_instance", None)
    class Stopped(AgentKernel):
        id = "rollback_stopped_test"
        def run(self, host, request):
            observed = host.execute(ToolCall("read", "file_read", {"path": "a.txt"}))
            assert observed.success, observed.data
            assert host.execute(ToolCall("write", "file_write", {"expected_version": observed.data["version"], "path": "a.txt", "content": "partial output"})).success
            raise RecoveryRequired("unknown cleanup") if uncertain else Cancelled()
    result = run_agent("write", None, AgentState(), workspace_dir=repo, chat_id="stopped", kernel=Stopped())
    review = checkpoint.review_checkpoint(repo, result._last_checkpoint)
    assert review["success"] != uncertain, review
    if not uncertain:
        assert checkpoint.restore_checkpoint(repo, result._last_checkpoint, review_token=review["review_token"])["success"]


def test_failed_attempt_without_file_changes_also_requires_new_review(repo, monkeypatch):
    from pathlib import Path
    cp = checkpoint.create_checkpoint(repo)
    (Path(repo) / "a.txt").write_text("agent", encoding="utf-8")
    checkpoint.seal_checkpoint(repo, cp["ref"])
    review = checkpoint.review_checkpoint(repo, cp["ref"])
    original = os.replace
    with monkeypatch.context() as patch:
        def fail(src, dest):
            if Path(dest).name == "a.txt":
                raise OSError("failed before first replacement")
            return original(src, dest)
        patch.setattr(checkpoint.os, "replace", fail)
        assert not checkpoint.restore_checkpoint(repo, cp["ref"], review_token=review["review_token"])["success"]
    refreshed = checkpoint.review_checkpoint(repo, cp["ref"])
    assert refreshed["success"] and refreshed["review_token"] != review["review_token"]
    assert not checkpoint.restore_checkpoint(repo, cp["ref"], review_token=review["review_token"])["success"]


def test_background_writes_prevent_checkpoint_end_sealing(repo):
    from contextlib import ExitStack
    from threading import Event
    from research_agent.background_tasks import BackgroundTasks
    cp = checkpoint.create_checkpoint(repo)
    manager = BackgroundTasks()
    entered, release = Event(), Event()
    def invoke(token):
        entered.set()
        while not release.wait(0.01):
            token.check()
        return {"success": True}
    manager.start(chat="background-seal", execution="background-seal", workspace=repo,
                  command="writes", backend="local", leases=ExitStack(), invoke=invoke)
    assert entered.wait(3)
    try:
        assert not checkpoint.seal_checkpoint_if_idle(repo, cp["ref"])["success"]
        assert not checkpoint.review_checkpoint(repo, cp["ref"])["success"]
    finally:
        manager.shutdown()
