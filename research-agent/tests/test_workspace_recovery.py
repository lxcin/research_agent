import json
from pathlib import Path

import pytest

from research_agent import session_workspace as sessions
from research_agent.workspace_recovery import inspect_recovery, recover_session


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    sessions.git(root, "init")
    sessions.git(root, "config", "user.name", "Recovery Test")
    sessions.git(root, "config", "user.email", "recovery@example.invalid")
    (root / "code.txt").write_text("original\n", encoding="utf-8")
    sessions.git(root, "add", ".")
    sessions.git(root, "commit", "-m", "initial")
    return root


def interrupt(monkeypatch, operation, point):
    command = "remove" if operation == "archive" else "add"
    original_git, original_publish = sessions.git, sessions._publish_binding
    def git(root, *args):
        if args[:2] == ("worktree", command):
            if point == "before_git":
                raise RuntimeError("simulated interruption")
            result = original_git(root, *args)
            if point == "after_git":
                raise RuntimeError("simulated interruption")
            return result
        return original_git(root, *args)
    def publish(path, value):
        original_publish(path, value)
        if point == "after_manifest" and path.name == "session.json":
            raise RuntimeError("simulated interruption")
    monkeypatch.setattr(sessions, "git", git)
    monkeypatch.setattr(sessions, "_publish_binding", publish)


@pytest.mark.parametrize("operation", ["create", "archive", "restore"])
@pytest.mark.parametrize("point", ["before_git", "after_git", "after_manifest"])
def test_interrupted_worktree_operation_can_be_reviewed_and_completed(project, monkeypatch, operation, point):
    chat = "recover"
    head = sessions.git(project, "rev-parse", "HEAD")
    if operation != "create":
        sessions.prepare_session(str(project), chat)
    if operation == "restore":
        sessions.archive_session(str(project), chat, expected_project_head=head, expected_session_head=head)
    with monkeypatch.context() as patch:
        interrupt(patch, operation, point)
        with pytest.raises(RuntimeError, match="simulated"):
            if operation == "create":
                sessions.prepare_session(str(project), chat)
            elif operation == "archive":
                sessions.archive_session(str(project), chat, expected_project_head=head, expected_session_head=head)
            else:
                sessions.restore_session(str(project), chat)
    with pytest.raises(RuntimeError, match="incomplete"):
        sessions.bound_session(chat)
    review = inspect_recovery(str(project), chat)
    assert review["issues"] == [] and "complete" in review["actions"], review
    result = recover_session(str(project), chat, review_token=review["review_token"], decision="complete")
    assert result["status"] == "recovered"
    binding = sessions.bound_session(chat)
    assert binding.archived == (operation == "archive")
    assert Path(binding.workspace).exists() != binding.archived
    assert sessions.git(project, "rev-parse", "HEAD") == head
    assert (project / "code.txt").read_text() == "original\n"


def test_cancel_unstarted_creation_removes_only_the_barrier(project, monkeypatch):
    with monkeypatch.context() as patch:
        interrupt(patch, "create", "before_git")
        with pytest.raises(RuntimeError):
            sessions.prepare_session(str(project), "cancel")
    review = inspect_recovery(str(project), "cancel")
    recover_session(str(project), "cancel", review_token=review["review_token"], decision="cancel")
    assert sessions.bound_session("cancel") is None
    binding = sessions.prepare_session(str(project), "cancel")
    assert Path(binding.workspace).is_dir()


def test_recovery_never_deletes_unknown_files_and_stale_review_is_rejected(project, monkeypatch):
    with monkeypatch.context() as patch:
        interrupt(patch, "create", "before_git")
        with pytest.raises(RuntimeError):
            sessions.prepare_session(str(project), "unknown")
    review = inspect_recovery(str(project), "unknown")
    _, checkout, _ = sessions._paths(str(project), "unknown")
    checkout.mkdir()
    (checkout / "human.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(RuntimeError, match="baseline changed"):
        recover_session(str(project), "unknown", review_token=review["review_token"], decision="complete")
    updated = inspect_recovery(str(project), "unknown")
    assert updated["actions"] == []
    assert (checkout / "human.txt").read_text() == "keep"


def test_completion_preserves_edits_in_registered_partial_checkout(project, monkeypatch):
    with monkeypatch.context() as patch:
        interrupt(patch, "create", "after_git")
        with pytest.raises(RuntimeError):
            sessions.prepare_session(str(project), "edited")
    _, checkout, _ = sessions._paths(str(project), "edited")
    (checkout / "code.txt").write_text("human edit after crash", encoding="utf-8")
    review = inspect_recovery(str(project), "edited")
    assert review["actions"] == ["complete"]
    recover_session(str(project), "edited", review_token=review["review_token"], decision="complete")
    assert (checkout / "code.txt").read_text() == "human edit after crash"


def test_cancel_archive_keeps_dirty_worktree_and_binding(project, monkeypatch):
    binding = sessions.prepare_session(str(project), "archive")
    head = sessions.git(project, "rev-parse", "HEAD")
    with monkeypatch.context() as patch:
        interrupt(patch, "archive", "before_git")
        with pytest.raises(RuntimeError):
            sessions.archive_session(str(project), "archive", expected_project_head=head, expected_session_head=head)
    (Path(binding.workspace) / ".env").write_text("preserve", encoding="utf-8")
    review = inspect_recovery(str(project), "archive")
    assert review["actions"] == ["cancel"]
    recover_session(str(project), "archive", review_token=review["review_token"], decision="cancel")
    assert sessions.bound_session("archive") == binding
    assert (Path(binding.workspace) / ".env").read_text() == "preserve"


def test_changed_branch_blocks_recovery(project, monkeypatch):
    with monkeypatch.context() as patch:
        interrupt(patch, "create", "after_git")
        with pytest.raises(RuntimeError):
            sessions.prepare_session(str(project), "moved")
    _, checkout, _ = sessions._paths(str(project), "moved")
    (checkout / "code.txt").write_text("new commit", encoding="utf-8")
    sessions.git(checkout, "add", ".")
    sessions.git(checkout, "commit", "-m", "human progress")
    review = inspect_recovery(str(project), "moved")
    assert not review["actions"] and "branch changed" in review["issues"][0]


def test_legacy_barrier_without_resources_can_be_cancelled(project):
    sessions._publish_binding(sessions._binding_path("legacy"),
                              {"status": "preparing", "chat_id": "legacy", "project": str(project)})
    review = inspect_recovery(str(project), "legacy")
    assert review["actions"] == ["cancel"]
    recover_session(str(project), "legacy", review_token=review["review_token"], decision="cancel")
    assert sessions.bound_session("legacy") is None


def test_recovery_cli_requires_explicit_review_token(project, monkeypatch):
    from click.testing import CliRunner
    from research_agent.cli import main
    with monkeypatch.context() as patch:
        interrupt(patch, "create", "before_git")
        with pytest.raises(RuntimeError):
            sessions.prepare_session(str(project), "cli")
    runner = CliRunner()
    review = runner.invoke(main, ["session-workspace", "recover-info", "--project", str(project), "--chat", "cli"])
    assert review.exit_code == 0, review.output
    token = json.loads(review.output)["review_token"]
    applied = runner.invoke(main, ["session-workspace", "recover", "--project", str(project), "--chat", "cli",
                                   "--decision", "complete", "--review-token", token])
    assert applied.exit_code == 0, applied.output
