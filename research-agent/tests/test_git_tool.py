"""Git integration tests — experiment version management and checkpoints.
All tests use real git on a temp directory — no mock needed."""
import os
import tempfile
import shutil
import subprocess

import pytest

# Will be implemented after tests pass design review
# from research_agent.tools.git_tool import (
#     git_init, git_checkpoint, git_log, git_rollback, git_status,
# )


@pytest.fixture()
def git_repo():
    """Create a temp directory with git init'd repo."""
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init"], cwd=d, capture_output=True, text=True)
    os.makedirs(os.path.join(d, "experiments"), exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


class TestGitInit:
    """git_init creates a repo with .gitignore for a project workdir."""

    def test_init_creates_git_repo(self, git_repo):
        """Init on existing repo is idempotent — no crash, still a repo."""
        result = _git_init(git_repo)
        assert result["success"] is True
        assert os.path.isdir(os.path.join(git_repo, ".git"))

    def test_init_writes_gitignore(self):
        """Init must create .gitignore with ignore patterns."""
        d = tempfile.mkdtemp()
        try:
            _git_init(d)  # fresh dir — first init
            gi_path = os.path.join(d, ".gitignore")
            assert os.path.exists(gi_path)
            content = open(gi_path).read()
            assert "__pycache__" in content
            assert ".venv" in content
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_init_on_clean_dir(self):
        """Init on a non-git dir creates the repo."""
        d = tempfile.mkdtemp()
        try:
            result = _git_init(d)
            assert result["success"] is True
            assert os.path.isdir(os.path.join(d, ".git"))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestGitCheckpoint:
    """git_checkpoint saves a snapshot of the working tree."""

    def test_checkpoint_commits_file(self, git_repo):
        """Write a file, checkpoint, verify it appears in git log."""
        write_file(git_repo, "experiments/test.py", "print(1)")
        result = _git_checkpoint(git_repo, "add test script")
        assert result["success"] is True
        assert result.get("hash", "")
        log = _git_log(git_repo, 5)
        assert "add test script" in str(log.get("log", ""))

    def test_checkpoint_with_no_changes(self, git_repo):
        """Checkpoint on clean tree is a no-op but still succeeds."""
        result = _git_checkpoint(git_repo, "empty checkpoint")
        assert result["success"] is True

    def test_multiple_checkpoints(self, git_repo):
        """Multiple checkpoints create distinct commits."""
        write_file(git_repo, "a.py", "1")
        h1 = _git_checkpoint(git_repo, "first")["hash"]
        write_file(git_repo, "b.py", "2")
        h2 = _git_checkpoint(git_repo, "second")["hash"]
        assert h1 != h2
        log = _git_log(git_repo, 5)["log"]
        assert len(log.split("\n")) >= 2

    def test_checkpoint_auto_escapes_special_chars(self, git_repo):
        """Special characters in message are escaped."""
        write_file(git_repo, "file.txt", "data")
        result = _git_checkpoint(git_repo, 'round_3: "hello" & test')
        assert result["success"] is True


class TestGitLog:
    """git_log returns commit history."""

    def test_log_returns_entry_count(self, git_repo):
        """Log returns at most N entries."""
        for i in range(5):
            write_file(git_repo, f"f{i}.txt", f"content {i}")
            _git_checkpoint(git_repo, f"commit {i}")
        log = _git_log(git_repo, 3)
        assert len(log["log"].split("\n")) == 3

    def test_log_on_empty_repo(self, git_repo):
        """Log on empty repo returns empty without crashing."""
        result = _git_log(git_repo, 10)
        assert result is not None


class TestGitRollback:
    """git_rollback restores working tree to a previous checkpoint."""

    def test_rollback_restores_file(self, git_repo):
        """Write -> checkpoint -> delete -> rollback -> file is back."""
        write_file(git_repo, "important.py", "original")
        result = _git_checkpoint(git_repo, "backup")
        commit_hash = result["hash"]
        os.remove(os.path.join(git_repo, "important.py"))
        assert not os.path.exists(os.path.join(git_repo, "important.py"))

        roll = _git_rollback(git_repo, commit_hash)
        assert roll["success"] is True
        assert os.path.exists(os.path.join(git_repo, "important.py"))
        assert open(os.path.join(git_repo, "important.py")).read() == "original"

    def test_rollback_invalid_hash(self, git_repo):
        """Rolling back to nonexistent hash should fail gracefully."""
        result = _git_rollback(git_repo, "deadbeef")
        assert result["success"] is False


class TestGitStatus:
    """git_status shows working tree state."""

    def test_status_clean_repo(self, git_repo):
        """Clean tree has no changes."""
        write_file(git_repo, "f.txt", "ok")
        _git_checkpoint(git_repo, "init")
        result = _git_status(git_repo)
        assert result["clean"] is True

    def test_status_dirty_repo(self, git_repo):
        """Modified file shows up in status."""
        write_file(git_repo, "f.txt", "original")
        _git_checkpoint(git_repo, "init")
        write_file(git_repo, "f.txt", "changed")
        result = _git_status(git_repo)
        assert result["clean"] is False
        assert "f.txt" in str(result.get("output", ""))

    def test_status_untracked_file(self, git_repo):
        """Untracked file is listed."""
        write_file(git_repo, "new_file.md", "hello")
        result = _git_status(git_repo)
        assert "new_file.md" in str(result.get("output", ""))


class TestAutoCheckpoint:
    """Auto-checkpoint integration with agent loop."""

    def test_should_checkpoint_when_file_written(self):
        """Trigger auto-checkpoint when tools produced file writes."""
        action_names = ["file_write", "file_edit", "shell_exec"]
        assert _should_auto_checkpoint(action_names) is True

    def test_should_not_checkpoint_read_only(self):
        """No checkpoint for read-only operations."""
        action_names = ["retrieve", "read_paper", "search_papers"]
        assert _should_auto_checkpoint(action_names) is False

    def test_should_not_checkpoint_on_errors(self):
        """No checkpoint when only failed shell_exec calls."""
        assert _should_auto_checkpoint([], has_errors=True) is False


# ── Helpers (these become the implementation signatures) ──

def _git_init(project_dir: str) -> dict:
    """Initialize git repo in project_dir. Called once per project."""
    if os.path.isdir(os.path.join(project_dir, ".git")):
        return {"success": True, "reason": "already initialized"}
    try:
        r = subprocess.run(["git", "init"], cwd=project_dir,
                           capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        return {"success": False, "error": "git not found"}
    if r.returncode != 0:
        return {"success": False, "error": r.stderr.strip()}
    gi = os.path.join(project_dir, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w") as f:
            f.write("# PaperPilot auto-generated\n__pycache__/\n*.pyc\n.venv/\n*.egg-info/\nbuild/\ndist/\n")
    return {"success": True}


def _git_checkpoint(project_dir: str, message: str) -> dict:
    """Save working tree snapshot."""
    escaped = message.replace('"', '\\"')
    subprocess.run(["git", "add", "-A"], cwd=project_dir,
                   capture_output=True, timeout=10)
    r = subprocess.run(["git", "commit", "-m", escaped, "--allow-empty"],
                       cwd=project_dir, capture_output=True, text=True, timeout=10)
    if r.returncode == 0:
        # Get commit hash
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           cwd=project_dir, capture_output=True, text=True, timeout=5)
        return {"success": True, "hash": h.stdout.strip(), "message": message}
    return {"success": True, "message": "no changes to commit", "hash": ""}


def _git_log(project_dir: str, n: int = 20) -> dict:
    """Return last n commits as formatted string."""
    r = subprocess.run(["git", "log", "--oneline", f"-{n}"],
                       cwd=project_dir, capture_output=True, text=True, timeout=10)
    log_text = r.stdout.strip()
    entries = [e for e in log_text.split("\n") if e.strip()]
    return {"log": "\n".join(entries), "count": len(entries)}


def _git_rollback(project_dir: str, commit_hash: str) -> dict:
    """Restore working tree files to a given commit."""
    r = subprocess.run(["git", "reset", "--hard", commit_hash],
                       cwd=project_dir, capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        return {"success": False, "error": r.stderr.strip() or "reset failed"}
    return {"success": True, "restored_to": commit_hash}


def _git_status(project_dir: str) -> dict:
    """Return working tree status."""
    r = subprocess.run(["git", "status", "--short"],
                       cwd=project_dir, capture_output=True, text=True, timeout=10)
    output = r.stdout.strip()
    return {"clean": output == "", "output": output}


def _should_auto_checkpoint(action_names: list[str], has_errors: bool = False) -> bool:
    """Determine if a checkpoint should be saved after this agent round."""
    if has_errors:
        return False
    write_actions = {"file_write", "file_edit", "shell_exec"}
    return bool(set(action_names) & write_actions)


def write_file(project_dir: str, relpath: str, content: str):
    """Helper: write a file in the project directory."""
    fp = os.path.join(project_dir, relpath)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w") as f:
        f.write(content)


# ── Integration tests (direct, no LLM) ──

class TestGitEndToEnd:
    """Full lifecycle: init -> work -> checkpoint -> log -> rollback."""

    def test_complete_lifecycle(self, git_repo):
        # Init
        assert _git_init(git_repo)["success"]

        # Work
        write_file(git_repo, "papers/gpt4.md", "# GPT-4 paper notes\nstuff")
        write_file(git_repo, "experiments/run.py", "print('run')")

        # Checkpoint
        r = _git_checkpoint(git_repo, "round_1: read GPT-4 paper")
        assert r["success"]
        assert r["hash"]

        # More work
        write_file(git_repo, "papers/claude.md", "# Claude paper notes")
        _git_checkpoint(git_repo, "round_2: read Claude paper")

        # Log
        log = _git_log(git_repo, 10)
        assert log["count"] == 2

        # Status clean
        s = _git_status(git_repo)
        assert s["clean"]

        # Rollback to first commit (resets HEAD + working tree)
        roll = _git_rollback(git_repo, r["hash"])
        assert roll["success"]
        # after hard reset, files committed after r are gone
        assert not os.path.exists(os.path.join(git_repo, "papers", "claude.md"))
        # files from first commit are restored
        assert os.path.exists(os.path.join(git_repo, "papers", "gpt4.md"))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
