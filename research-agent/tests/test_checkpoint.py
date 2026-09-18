# tests/test_checkpoint.py — workspace snapshot + rollback (git-based)
import os
import shutil
import subprocess

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

    res = checkpoint.restore_checkpoint(repo, cp["ref"])
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
    checkpoint.restore_checkpoint(repo, cp["ref"])
    # rollback restores the AGENT-EDIT state, not HEAD ("v1")
    assert open(os.path.join(repo, "a.txt"), encoding="utf-8").read() == "agent-edit"


def test_list_and_unknown_restore(repo):
    assert checkpoint.create_checkpoint(repo)["success"]
    refs = checkpoint.list_checkpoints(repo)
    assert refs and refs[0]["ref"].startswith(checkpoint.REF_PREFIX)
    assert checkpoint.restore_checkpoint(repo, "refs/research-agent/checkpoints/nope")["success"] is False
