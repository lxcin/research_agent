# tests/test_proposals.py — git-based keep/undo proposal manager
import os
import pytest

from research_agent.proposal import ProposalManager
from research_agent.tools.git_tool import git_init, _run_git


@pytest.fixture
def repo(tmp_path):
    d = str(tmp_path)
    git_init(d)
    with open(os.path.join(d, "tracked.txt"), "w", encoding="utf-8") as f:
        f.write("original\n")
    _run_git(["add", "-A"], d)
    _run_git(["commit", "-m", "init"], d)
    return d


def test_non_repo_returns_empty(tmp_path):
    d = str(tmp_path / "notgit")
    os.makedirs(d)
    pm = ProposalManager(d)
    assert pm.is_repo() is False
    assert pm.collect() == []


def test_collect_modified_file(repo):
    with open(os.path.join(repo, "tracked.txt"), "w", encoding="utf-8") as f:
        f.write("original\nadded line\n")
    changes = ProposalManager(repo).collect()
    assert len(changes) == 1
    c = changes[0]
    assert c.path == "tracked.txt"
    assert c.status == "modified"
    assert c.additions >= 1
    assert "added line" in c.diff


def test_collect_added_file(repo):
    with open(os.path.join(repo, "new.txt"), "w", encoding="utf-8") as f:
        f.write("hello\nworld\n")
    changes = ProposalManager(repo).collect()
    assert any(c.path == "new.txt" and c.status == "added" for c in changes)


def test_undo_modified_restores_original(repo):
    with open(os.path.join(repo, "tracked.txt"), "w", encoding="utf-8") as f:
        f.write("changed\n")
    pm = ProposalManager(repo)
    pm.undo(["tracked.txt"])
    with open(os.path.join(repo, "tracked.txt"), encoding="utf-8") as f:
        assert f.read() == "original\n"


def test_undo_added_deletes_file(repo):
    path = os.path.join(repo, "new.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("temp\n")
    ProposalManager(repo).undo(["new.txt"])
    assert not os.path.exists(path)


def test_keep_commits_change(repo):
    with open(os.path.join(repo, "tracked.txt"), "w", encoding="utf-8") as f:
        f.write("kept\n")
    pm = ProposalManager(repo)
    res = pm.keep(["tracked.txt"], "accept test")
    assert res["success"]
    # working tree clean for that file
    assert pm.collect(["tracked.txt"]) == []
    with open(os.path.join(repo, "tracked.txt"), encoding="utf-8") as f:
        assert f.read() == "kept\n"


def test_keep_then_undo_does_not_revert_committed(repo):
    """After keep, the change is committed → undo should not lose it."""
    with open(os.path.join(repo, "tracked.txt"), "w", encoding="utf-8") as f:
        f.write("kept\n")
    pm = ProposalManager(repo)
    pm.keep(["tracked.txt"])
    pm.undo(["tracked.txt"])  # nothing to revert (clean)
    with open(os.path.join(repo, "tracked.txt"), encoding="utf-8") as f:
        assert f.read() == "kept\n"


def test_keep_all_and_undo_all(repo):
    for name, body in (("a.txt", "A\n"), ("b.txt", "B\n")):
        with open(os.path.join(repo, name), "w", encoding="utf-8") as f:
            f.write(body)
    pm = ProposalManager(repo)
    assert len(pm.collect()) == 2
    pm.keep_all("accept all")
    assert pm.collect() == []

    with open(os.path.join(repo, "a.txt"), "w", encoding="utf-8") as f:
        f.write("A2\n")
    with open(os.path.join(repo, "c.txt"), "w", encoding="utf-8") as f:
        f.write("C\n")
    pm.undo_all()
    assert pm.collect() == []
    with open(os.path.join(repo, "a.txt"), encoding="utf-8") as f:
        assert f.read() == "A\n"          # restored
    assert not os.path.exists(os.path.join(repo, "c.txt"))  # deleted
