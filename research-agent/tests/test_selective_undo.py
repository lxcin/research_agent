"""Content and lifecycle acceptance using real, isolated Git repositories."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from research_agent import checkpoint as cp
from research_agent.execution import RecoveryRequired
from research_agent.rollback import apply_undo, cancel_conflict, review_undo
from research_agent.review_baseline import baseline


def git(ws, *args):
    result = subprocess.run(["git", *args], cwd=ws, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def repo(tmp_path):
    ws = tmp_path / "repo"
    ws.mkdir()
    git(ws, "init", "-q")
    git(ws, "config", "user.name", "test")
    git(ws, "config", "user.email", "test@example.com")
    git(ws, "config", "core.autocrlf", "false")
    (ws / "a.txt").write_bytes(b"first\nsecond\nthird\nfourth\nfifth\n")
    (ws / "b.txt").write_bytes(b"other\n")
    git(ws, "add", "-A")
    git(ws, "commit", "-qm", "initial")
    return str(ws)


def start(ws):
    state = SimpleNamespace(_execution_id="test-execution")
    return cp.snapshot_for_run(state, ws)


def seal(ws, ref):
    result = cp.seal_checkpoint(ws, ref)
    assert result["success"], result


def undo(ws, ref, paths=None):
    review = review_undo(ws, ref, paths)
    assert review["success"], review
    result = apply_undo(ws, review_token=review["review_token"])
    assert result["success"], result
    return result


def test_reverse_merge_keeps_human_before_and_after(repo):
    path = Path(repo) / "a.txt"
    path.write_bytes(path.read_bytes().replace(b"first", b"human-before"))
    git(repo, "add", "a.txt")
    original_index = cp._index_state(repo)["data"]
    ref = start(repo)
    path.write_bytes(path.read_bytes().replace(b"third", b"agent"))
    seal(repo, ref)
    path.write_bytes(path.read_bytes().replace(b"fifth", b"human-after"))
    undo(repo, ref)
    assert path.read_bytes() == b"human-before\nsecond\nthird\nfourth\nhuman-after\n"
    assert cp._index_state(repo)["data"] == original_index


def test_selection_preserves_other_files_and_staging(repo):
    ref = start(repo)
    (Path(repo) / "a.txt").write_bytes(b"agent-a\n")
    (Path(repo) / "b.txt").write_bytes(b"agent-b\n")
    git(repo, "add", "-A")
    seal(repo, ref)
    undo(repo, ref, ["a.txt"])
    assert git(repo, "show", ":a.txt").startswith("first")
    assert git(repo, "show", ":b.txt") == "agent-b\n"
    assert (Path(repo) / "b.txt").read_bytes() == b"agent-b\n"
    undo(repo, ref, ["b.txt"])
    assert git(repo, "status", "--porcelain") == ""


def test_conflict_can_be_explicitly_cancelled_without_writing(repo):
    path = Path(repo) / "a.txt"
    ref = start(repo)
    path.write_bytes(b"agent\n")
    seal(repo, ref)
    path.write_bytes(b"human\n")
    review = review_undo(repo, ref)
    assert not review["success"] and review["conflicts"]
    assert path.read_bytes() == b"human\n"
    artifact = Path(review["conflicts"][0]["result"])
    assert not artifact.is_relative_to(Path(repo))
    assert b"<<<<<<<" in artifact.read_bytes()
    assert not cancel_conflict(repo, review_token="forged")["success"]
    assert cancel_conflict(repo, review_token=review["review_token"])["success"]
    assert start(repo)
    assert not artifact.exists()


def test_conflict_resolution_requires_new_review_and_is_bound(repo):
    path = Path(repo) / "a.txt"
    ref = start(repo)
    path.write_bytes(b"agent\n")
    seal(repo, ref)
    path.write_bytes(b"human\n")
    conflict = review_undo(repo, ref)
    artifact = Path(conflict["conflicts"][0]["result"])
    artifact.write_bytes(b"chosen human result\n")
    review = review_undo(repo, ref, resolved=["a.txt"])
    assert review["success"], review
    assert not apply_undo(repo, review_token=conflict["review_token"])["success"]
    artifact.write_bytes(b"changed after review\n")
    assert not apply_undo(repo, review_token=review["review_token"])["success"]
    review = review_undo(repo, ref, resolved=["a.txt"])
    assert review["success"], review
    assert apply_undo(repo, review_token=review["review_token"])["success"]
    assert path.read_bytes() == b"changed after review\n"


@pytest.mark.parametrize("kind", ["added", "deleted", "binary"])
def test_changed_nonmergeable_files_conflict(repo, kind):
    path = Path(repo) / "a.txt"
    ref = start(repo)
    if kind == "added":
        path = Path(repo) / "new.txt"
        path.write_bytes(b"agent")
    elif kind == "deleted":
        path.unlink()
    else:
        path.write_bytes(b"agent\0binary")
    seal(repo, ref)
    path.write_bytes(b"human\0binary" if kind == "binary" else b"human")
    before = path.read_bytes()
    review = review_undo(repo, ref)
    assert not review["success"] and review["conflicts"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("content", [b"one\r\ntwo\r\n", b"\xef\xbb\xbfone\nlast", b"data\0binary"])
def test_raw_bytes_restore_with_autocrlf(repo, content):
    git(repo, "config", "core.autocrlf", "true")
    path = Path(repo) / "a.txt"
    path.write_bytes(content)
    ref = start(repo)
    path.write_bytes(b"agent\n")
    seal(repo, ref)
    undo(repo, ref)
    assert path.read_bytes() == content


def test_new_round_retires_refs_after_saving_actual_human_baseline(repo):
    path = Path(repo) / "a.txt"
    old = start(repo)
    path.write_bytes(b"agent")
    seal(repo, old)
    path.write_bytes(b"human between turns")
    new = start(repo)
    assert [r["ref"] for r in cp.list_checkpoints(repo)] == [new]
    assert git(repo, "for-each-ref", "refs/research-agent/ends/") == ""
    assert not cp.review_checkpoint(repo, old)["success"]
    path.write_bytes(b"second agent")
    seal(repo, new)
    undo(repo, new)
    assert path.read_bytes() == b"human between turns"


def test_snapshot_failure_keeps_old_refs(repo, monkeypatch):
    ref = start(repo)
    seal(repo, ref)
    monkeypatch.setattr(cp, "create_checkpoint", lambda *a, **k: {"success": False, "reason": "disk full"})
    with pytest.raises(RecoveryRequired):
        start(repo)
    assert cp.list_checkpoints(repo)[0]["ref"] == ref


def test_unsealed_turn_does_not_block_fresh_current_baseline(repo):
    old = start(repo)
    path = Path(repo) / "a.txt"
    path.write_bytes(b"previous agent and human changes")
    git(repo, "add", "a.txt")
    original_index = cp._index_state(repo)["data"]
    path.write_bytes(b"additional unstaged human edit")
    new = start(repo)
    assert [item["ref"] for item in cp.list_checkpoints(repo)] == [new]
    assert cp._index_state(repo)["data"] == original_index
    assert not cp.review_checkpoint(repo, old)["success"]
    path.write_bytes(b"new agent edit")
    git(repo, "add", "a.txt")
    seal(repo, new)
    undo(repo, new)
    assert path.read_bytes() == b"additional unstaged human edit"
    # Selective index restoration may invalidate Git's stat cache; its staged
    # content, mode and membership must match, not its cache bytes.
    assert git(repo, "show", ":a.txt") == "previous agent and human changes"
    assert git(repo, "diff", "--cached", "--name-only").strip() == "a.txt"


def test_new_turn_expires_conflict_and_preserves_resolution_across_turns(repo):
    path = Path(repo) / "a.txt"
    old = start(repo)
    path.write_bytes(b"agent")
    seal(repo, old)
    path.write_bytes(b"human")
    conflict = review_undo(repo, old)
    artifact = Path(conflict["conflicts"][0]["result"])
    artifact.write_bytes(b"human resolution in progress")
    new = start(repo)
    assert path.read_bytes() == b"human"
    assert not baseline(repo, "selective-undo")
    assert not baseline(repo, "checkpoint-rollback")
    assert not apply_undo(repo, review_token=conflict["review_token"])["success"]
    assert not review_undo(repo, old)["success"]
    seal(repo, new)
    start(repo)
    assert artifact.read_bytes() == b"human resolution in progress"


def test_failed_new_snapshot_preserves_conflict_and_resolution(repo, monkeypatch):
    path = Path(repo) / "a.txt"
    old = start(repo)
    path.write_bytes(b"agent")
    seal(repo, old)
    path.write_bytes(b"human")
    conflict = review_undo(repo, old)
    artifact = Path(conflict["conflicts"][0]["result"])
    artifact.write_bytes(b"resolution")
    active = baseline(repo, "active-turn")
    stored = baseline(repo, "selective-undo")
    monkeypatch.setattr(cp, "create_checkpoint", lambda *a, **k: {"success": False, "reason": "disk full"})
    with pytest.raises(RecoveryRequired):
        start(repo)
    assert baseline(repo, "active-turn") == active
    assert baseline(repo, "selective-undo") == stored
    assert cp.list_checkpoints(repo)[0]["ref"] == old
    assert artifact.read_bytes() == b"resolution"


def test_stale_token_staging_and_foreign_lock_rejected(repo):
    ref = start(repo)
    path = Path(repo) / "a.txt"
    path.write_bytes(b"agent")
    seal(repo, ref)
    review = review_undo(repo, ref)
    assert review["success"]
    lock = Path(cp._index_state(repo)["path"] + ".lock")
    lock.write_bytes(b"foreign")
    assert not apply_undo(repo, review_token=review["review_token"])["success"]
    assert lock.read_bytes() == b"foreign"
    lock.unlink()
    path.write_bytes(b"human")
    assert not apply_undo(repo, review_token=review["review_token"])["success"]
    git(repo, "add", "a.txt")
    assert not review_undo(repo, ref)["success"]


def test_partial_apply_reuses_original_candidate_and_needs_new_token(repo, monkeypatch):
    ref = start(repo)
    for name in ("a.txt", "b.txt"):
        (Path(repo) / name).write_bytes(b"agent\n")
    seal(repo, ref)
    review = review_undo(repo, ref)
    real_replace = os.replace
    def fail(source, target):
        if str(target).endswith("b.txt"):
            raise OSError("injected failure")
        return real_replace(source, target)
    with monkeypatch.context() as m:
        m.setattr(cp.os, "replace", fail)
        result = apply_undo(repo, review_token=review["review_token"])
    assert not result["success"] and result["partial"]
    with pytest.raises(RecoveryRequired):
        start(repo)
    refreshed = review_undo(repo, ref)
    assert refreshed["success"] and refreshed["resume"], refreshed
    assert not apply_undo(repo, review_token=review["review_token"])["success"]
    assert apply_undo(repo, review_token=refreshed["review_token"])["success"]
    assert git(repo, "status", "--porcelain") == ""


@pytest.mark.parametrize("crash_target", ["file", "index"])
def test_process_crash_after_file_write_can_resume(repo, crash_target):
    ref = start(repo)
    for name in ("a.txt", "b.txt"):
        (Path(repo) / name).write_bytes(b"agent\n")
    git(repo, "add", "-A")
    seal(repo, ref)
    review = review_undo(repo, ref)
    script = '''
import os, sys
from research_agent import checkpoint as cp
from research_agent.rollback import apply_undo
real = os.replace
def crash(source, target):
    real(source, target)
    if (sys.argv[3] == 'file' and str(target).endswith('a.txt')) or (sys.argv[3] == 'index' and str(target).endswith('index')):
        os._exit(17)
cp.os.replace = crash
apply_undo(sys.argv[1], review_token=sys.argv[2])
'''
    result = subprocess.run([sys.executable, "-c", script, repo, review["review_token"], crash_target], capture_output=True)
    assert result.returncode == 17, result.stderr
    refreshed = review_undo(repo, ref)
    assert refreshed["success"] and refreshed["resume"], refreshed
    assert refreshed["recover_owned_index_lock"] == (crash_target == "file")
    assert apply_undo(repo, review_token=refreshed["review_token"])["success"]
    assert git(repo, "status", "--porcelain") == ""


def test_proposal_uses_turn_diff_and_unified_undo(repo):
    from research_agent.proposal import ProposalManager
    path = Path(repo) / "a.txt"
    path.write_bytes(b"human-before\n")
    ref = start(repo)
    path.write_bytes(b"human-before\nagent\n")
    seal(repo, ref)
    manager = ProposalManager(repo)
    changes = manager.collect()
    assert len(changes) == 1
    assert "+agent" in changes[0].diff and "-first" not in changes[0].diff
    result = manager.undo_all(review_token=manager.review_token)
    assert result["success"], result
    assert path.read_bytes() == b"human-before\n"


def test_cli_undo_review_apply(repo):
    from click.testing import CliRunner
    from research_agent.cli import main
    ref = start(repo)
    (Path(repo) / "b.txt").write_bytes(b"agent")
    seal(repo, ref)
    runner = CliRunner()
    result = runner.invoke(main, ["checkpoint", "undo-review", "--workspace", repo, "--ref", ref])
    assert result.exit_code == 0, result.output
    token = json.loads(result.output)["review_token"]
    result = runner.invoke(main, ["checkpoint", "undo-apply", "--workspace", repo, "--review-token", token])
    assert result.exit_code == 0, result.output
    assert (Path(repo) / "b.txt").read_bytes() == b"other\n"


def test_api_uses_chat_snapshot_and_same_review_contract(repo):
    from fastapi.testclient import TestClient
    from research_agent.server import app
    state = SimpleNamespace(_execution_id="api-execution", active_chat_id="api-chat")
    ref = cp.snapshot_for_run(state, repo)
    (Path(repo) / "b.txt").write_bytes(b"agent")
    seal(repo, ref)
    client = TestClient(app)
    body = {"workspace_dir": repo, "paths": ["b.txt"]}
    assert client.post("/api/chats/another/undo/review", json=body).status_code == 409
    response = client.post("/api/chats/api-chat/undo/review", json=body)
    assert response.status_code == 200, response.text
    body = {"workspace_dir": repo, "review_token": response.json()["review_token"]}
    response = client.post("/api/chats/api-chat/undo/apply", json=body)
    assert response.status_code == 200, response.text
    assert (Path(repo) / "b.txt").read_bytes() == b"other\n"


def test_file_only_agent_round_is_snapshotted_and_proposal_has_token(repo, monkeypatch):
    from research_agent.agent import run_agent
    from research_agent.kernel import AgentKernel, KernelResult, ToolCall
    from research_agent.models import AgentState
    from research_agent.tools import ToolRegistry
    import research_agent.tools as tool_module
    monkeypatch.setattr(ToolRegistry, "_instance", None)
    enabled = tool_module.is_plugin_enabled
    monkeypatch.setattr(tool_module, "is_plugin_enabled", lambda name: False if name == "shell" else enabled(name))
    class FileKernel(AgentKernel):
        id = "file-only-undo-test"
        def run(self, host, request):
            observed = host.execute(ToolCall("read", "file_read", {"path": "b.txt"}))
            assert observed.success, observed.data
            assert host.execute(ToolCall("write", "file_write", {"expected_version": observed.data["version"], "path": "b.txt", "content": "agent"})).success
            return KernelResult("written")
    result = run_agent("write", None, AgentState(), workspace_dir=repo, chat_id="file-only", kernel=FileKernel())
    assert result.termination_reason != "recovery_required", result.error
    assert result._last_checkpoint
    assert result.pending_proposals and result.pending_proposals[0].review_token
    undo(repo, result._last_checkpoint)
    assert (Path(repo) / "b.txt").read_bytes() == b"other\n"


def test_selection_does_not_reject_unrelated_human_file_edits(repo):
    ref = start(repo)
    (Path(repo) / "a.txt").write_bytes(b"agent")
    seal(repo, ref)
    (Path(repo) / "b.txt").write_bytes(b"human-after")
    (Path(repo) / "human.txt").write_bytes(b"human-new")
    undo(repo, ref, ["a.txt"])
    assert (Path(repo) / "b.txt").read_bytes() == b"human-after"
    assert (Path(repo) / "human.txt").read_bytes() == b"human-new"


def test_pure_staging_change_can_be_selected(repo):
    path = Path(repo) / "a.txt"
    path.write_bytes(b"human-unstaged")
    ref = start(repo)
    git(repo, "add", "a.txt")
    seal(repo, ref)
    undo(repo, ref, ["a.txt"])
    assert path.read_bytes() == b"human-unstaged"
    assert git(repo, "diff", "--cached") == ""


def test_untracked_human_file_and_agent_staged_addition(repo):
    human = Path(repo) / "human.txt"
    human.write_bytes(b"human-original")
    ref = start(repo)
    human.write_bytes(b"human-original\nagent")
    (Path(repo) / "new.txt").write_bytes(b"agent-new")
    git(repo, "add", "human.txt", "new.txt")
    seal(repo, ref)
    undo(repo, ref)
    assert human.read_bytes() == b"human-original"
    assert "?? human.txt" in git(repo, "status", "--porcelain")
    assert not (Path(repo) / "new.txt").exists()


def test_empty_agent_delta_keeps_human_edits(repo):
    ref = start(repo)
    seal(repo, ref)
    (Path(repo) / "a.txt").write_bytes(b"human-after")
    undo(repo, ref)
    assert (Path(repo) / "a.txt").read_bytes() == b"human-after"


def test_cross_worktree_review_and_token_are_rejected(repo, tmp_path):
    ref = start(repo)
    (Path(repo) / "a.txt").write_bytes(b"agent")
    seal(repo, ref)
    review = review_undo(repo, ref)
    other = str(tmp_path / "other")
    git(repo, "worktree", "add", "-b", "other", other)
    assert not review_undo(other, ref)["success"]
    assert not apply_undo(other, review_token=review["review_token"])["success"]
    start(other)
    assert cp.list_checkpoints(repo)[0]["ref"] == ref


def test_agent_deleted_file_is_restored_and_human_deleted_modified_file_conflicts(repo):
    path = Path(repo) / "a.txt"
    original = path.read_bytes()
    ref = start(repo)
    path.unlink()
    git(repo, "add", "-A")
    seal(repo, ref)
    undo(repo, ref)
    assert path.read_bytes() == original
    ref = start(repo)
    path.write_bytes(b"agent")
    seal(repo, ref)
    path.unlink()
    review = review_undo(repo, ref)
    assert not review["success"] and review["conflicts"]
    assert not path.exists()


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_merge_preserves_bom_newlines_and_no_final_newline(newline):
    from research_agent.rollback import _merge
    original = b"\xef\xbb\xbfone|two|three|four|five".replace(b"|", newline)
    end = original.replace(b"three", b"agent")
    current = end.replace(b"five", b"human")
    merged, conflict = _merge(current, end, original)
    assert not conflict
    assert merged == original.replace(b"five", b"human")


def test_ignored_collision_and_head_change_rejected(repo):
    path = Path(repo) / "a.txt"
    ref = start(repo)
    path.unlink()
    git(repo, "rm", "--cached", "a.txt")
    seal(repo, ref)
    # External ignore rules must not allow an unobserved occupant to be overwritten.
    exclude = Path(git(repo, "rev-parse", "--absolute-git-dir").strip()) / "info" / "exclude"
    exclude.write_text("a.txt\n", encoding="utf-8")
    path.write_bytes(b"human ignored")
    assert not review_undo(repo, ref)["success"]
    assert path.read_bytes() == b"human ignored"
    path.unlink()
    git(repo, "commit", "-qm", "external head change")
    assert not review_undo(repo, ref)["success"]


def test_unknown_file_in_artifact_directory_survives_cleanup(repo):
    ref = start(repo)
    path = Path(repo) / "a.txt"
    path.write_bytes(b"agent")
    seal(repo, ref)
    path.write_bytes(b"human")
    conflict = review_undo(repo, ref)
    directory = Path(conflict["conflicts"][0]["directory"])
    extra = directory / "human-note"
    extra.write_bytes(b"keep this")
    assert cancel_conflict(repo, review_token=conflict["review_token"])["success"]
    start(repo)
    assert extra.read_bytes() == b"keep this"


def test_staged_objects_remain_reachable_even_after_git_prune(repo):
    path = Path(repo) / "a.txt"
    path.write_bytes(b"human-staged")
    git(repo, "add", "a.txt")
    staged_oid = git(repo, "rev-parse", ":a.txt").strip()
    path.write_bytes(b"human-working")
    ref = start(repo)
    path.write_bytes(b"agent")
    git(repo, "add", "a.txt")
    seal(repo, ref)
    # This is an isolated temporary repository, never the developer repository.
    git(repo, "prune", "--expire=now")
    assert git(repo, "cat-file", "-p", staged_oid) == "human-staged"
    undo(repo, ref)
    assert path.read_bytes() == b"human-working"
    assert git(repo, "show", ":a.txt") == "human-staged"


def test_intent_to_add_is_not_silently_rewritten(repo):
    path = Path(repo) / "new.txt"
    path.write_bytes(b"human-intent-to-add")
    git(repo, "add", "-N", "new.txt")
    ref = start(repo)
    path.write_bytes(b"agent")
    seal(repo, ref)
    before = cp._index_state(repo)["hash"]
    result = review_undo(repo, ref)
    assert not result["success"] and "flags" in result["reason"], result
    assert cp._index_state(repo)["hash"] == before
