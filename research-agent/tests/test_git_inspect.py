import subprocess
from types import SimpleNamespace

import pytest

from research_agent.tools.builtin.git_inspect import inspect_git, _handle


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "example.txt").write_text("before\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    worktree = tmp_path / "session"
    git(root, "worktree", "add", "-b", "codex/test", str(worktree))
    return root, worktree


def test_host_git_reads_linked_worktree_without_touching_source(checkout, monkeypatch):
    source, worktree = checkout
    (worktree / "example.txt").write_text("after\n", encoding="utf-8")
    monkeypatch.setenv("GIT_DIR", str(source / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(source))
    status = inspect_git(str(worktree))
    assert status["success"] and "codex/test" in status["stdout"]
    assert "example.txt" in status["stdout"]
    assert "+after" in inspect_git(str(worktree), view="diff")["stdout"]
    assert "initial" in inspect_git(str(worktree), view="log")["stdout"]
    assert "+before" in inspect_git(str(worktree), view="show")["stdout"]
    assert (source / "example.txt").read_text() == "before\n"


@pytest.mark.parametrize("params", [
    {"view": "reset"}, {"revision": "--output=bad"}, {"revision": "../HEAD"},
    {"path": "../outside"}, {"path": ":(top)**"}, {"limit": True}, {"limit": 101},
])
def test_host_git_rejects_untyped_or_escaping_arguments(checkout, params):
    _, worktree = checkout
    result = _handle(params, None, SimpleNamespace(workspace_dir=str(worktree)), lambda *args: None)
    assert not result.success


def test_git_tool_is_injected_as_read_only_host_capability():
    from research_agent.tools.builtin import _plugins
    plugin = next(plugin for plugin in _plugins() if plugin.id == "filesystem")
    tool = next(tool for tool in plugin.tools if tool.name == "git_inspect")
    assert not tool.side_effect and not tool.requires_approval
    assert "Docker" in tool.to_openai_schema()["function"]["description"]
