"""Test filesystem tools: write, read, edit, glob, grep — all scoped to workspace."""
import os
import tempfile
import shutil

import pytest
from research_agent.models import AgentState
from research_agent.tools.builtin.filesystem import (
    _handle_file_write, _handle_file_read, _handle_file_edit,
    _handle_file_glob, _handle_file_grep, _handle_shell_exec,
    _get_project_dir, _safe_path,
)
from research_agent.tools.schema import ToolResult


def _mock_emit(et: str, d: dict):
    pass


@pytest.fixture
def workspace():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def state(workspace):
    s = AgentState(user_input="test")
    s.workspace_dir = workspace
    return s


def test_get_project_dir_uses_workspace(workspace, state):
    d = _get_project_dir(state)
    assert d == workspace


def test_get_project_dir_fallback(workspace):
    s = AgentState(user_input="test")
    d = _get_project_dir(s)
    assert os.path.isdir(d)


def test_safe_path_allows_inside(workspace):
    p = _safe_path(workspace, "subdir/file.txt")
    assert p is not None
    assert p.endswith("file.txt")


def test_safe_path_blocks_escape(workspace):
    p = _safe_path(workspace, "../../etc/passwd")
    assert p is None


def test_file_write_creates_file(workspace, state):
    result = _handle_file_write(
        {"path": "hello.txt", "content": "Hello World"},
        None, state, _mock_emit,
    )
    assert result.success
    assert os.path.isfile(os.path.join(workspace, "hello.txt"))
    with open(os.path.join(workspace, "hello.txt")) as f:
        assert f.read() == "Hello World"


def test_file_write_subdirectory(workspace, state):
    result = _handle_file_write(
        {"path": "sub/deep/file.py", "content": "print(1)"},
        None, state, _mock_emit,
    )
    assert result.success
    filepath = os.path.join(workspace, "sub", "deep", "file.py")
    assert os.path.isfile(filepath)
    with open(filepath) as f:
        assert f.read() == "print(1)"


def test_file_read_returns_content(workspace, state):
    filepath = os.path.join(workspace, "readme.md")
    with open(filepath, "w") as f:
        f.write("# README\nContent here.")
    result = _handle_file_read(
        {"path": "readme.md"},
        None, state, _mock_emit,
    )
    assert result.success
    assert "# README" in result.data["content"]
    assert result.data["size"] > 0
    assert result.data["lines"] >= 2


def test_file_read_nonexistent(workspace, state):
    result = _handle_file_read(
        {"path": "nope.txt"},
        None, state, _mock_emit,
    )
    assert not result.success


def test_file_read_outside_workspace(workspace, state):
    result = _handle_file_read(
        {"path": "../../etc/passwd"},
        None, state, _mock_emit,
    )
    assert not result.success


def test_file_edit_replaces_unique_string(workspace, state):
    filepath = os.path.join(workspace, "config.py")
    with open(filepath, "w") as f:
        f.write("VERSION = '1.0'\nDEBUG = False\n")
    result = _handle_file_edit(
        {"path": "config.py", "old_string": "DEBUG = False", "new_string": "DEBUG = True"},
        None, state, _mock_emit,
    )
    assert result.success
    assert result.data.get("replaced") is True
    with open(filepath) as f:
        content = f.read()
    assert "DEBUG = True" in content
    assert "DEBUG = False" not in content


def test_file_edit_fails_ambiguous(workspace, state):
    filepath = os.path.join(workspace, "dup.txt")
    with open(filepath, "w") as f:
        f.write("hello world\nhello world\n")
    result = _handle_file_edit(
        {"path": "dup.txt", "old_string": "hello world", "new_string": "bye"},
        None, state, _mock_emit,
    )
    assert not result.success


def test_file_glob_finds_files(workspace, state):
    os.makedirs(os.path.join(workspace, "src"), exist_ok=True)
    with open(os.path.join(workspace, "a.py"), "w") as f: f.write("1")
    with open(os.path.join(workspace, "src", "b.py"), "w") as f: f.write("2")
    with open(os.path.join(workspace, "readme.md"), "w") as f: f.write("md")

    result = _handle_file_glob(
        {"pattern": "**/*.py"},
        None, state, _mock_emit,
    )
    assert result.success
    matches = [m.replace("\\", "/") for m in result.data["matches"]]
    assert "a.py" in matches
    assert "src/b.py" in matches
    assert result.data["count"] >= 2


def test_file_glob_excludes_git(workspace, state):
    os.makedirs(os.path.join(workspace, ".git"), exist_ok=True)
    with open(os.path.join(workspace, ".git", "config"), "w") as f: f.write("x")

    result = _handle_file_glob(
        {"pattern": "**/*"},
        None, state, _mock_emit,
    )
    assert result.success
    for m in result.data["matches"]:
        assert not m.startswith(".git")


def test_file_grep_finds_pattern(workspace, state):
    with open(os.path.join(workspace, "code.py"), "w") as f:
        f.write("def foo():\n    return 42\n\ndef bar():\n    return 99\n")
    result = _handle_file_grep(
        {"pattern": r"def \w+", "include": "*.py"},
        None, state, _mock_emit,
    )
    assert result.success
    assert result.data["count"] >= 2
    contents = [m["content"] for m in result.data["matches"]]
    assert any("def foo" in c for c in contents)
    assert any("def bar" in c for c in contents)


def test_shell_exec_runs_in_workspace(workspace, state):
    result = _handle_shell_exec(
        {"command": "echo hello>test_out.txt"},
        None, state, _mock_emit,
    )
    assert result.success
    filepath = os.path.join(workspace, "test_out.txt")
    assert os.path.isfile(filepath)
    with open(filepath) as f:
        assert "hello" in f.read()


def test_shell_exec_blocks_sudo(workspace, state):
    result = _handle_shell_exec(
        {"command": "sudo rm -rf /"},
        None, state, _mock_emit,
    )
    assert not result.success


def test_write_then_read_roundtrip(workspace, state):
    content = "The quick brown fox\njumps over\nthe lazy dog."
    _handle_file_write(
        {"path": "fox.txt", "content": content},
        None, state, _mock_emit,
    )
    result = _handle_file_read(
        {"path": "fox.txt"},
        None, state, _mock_emit,
    )
    assert result.success
    assert result.data["content"] == content
    assert result.data["lines"] == 3


def test_workspace_isolation(workspace, state):
    """Files written in one workspace do not appear in another."""
    _handle_file_write(
        {"path": "secret.txt", "content": "top secret"},
        None, state, _mock_emit,
    )
    ws2 = tempfile.mkdtemp()
    try:
        s2 = AgentState(user_input="test")
        s2.workspace_dir = ws2
        result = _handle_file_glob(
            {"pattern": "*"},
            None, s2, _mock_emit,
        )
        assert "secret.txt" not in result.data.get("matches", [])
    finally:
        shutil.rmtree(ws2, ignore_errors=True)


def test_filename_with_spaces(workspace, state):
    _handle_file_write(
        {"path": "my research notes.md", "content": "# Notes\nFindings here."},
        None, state, _mock_emit,
    )
    filepath = os.path.join(workspace, "my research notes.md")
    assert os.path.isfile(filepath)
    result = _handle_file_read(
        {"path": "my research notes.md"},
        None, state, _mock_emit,
    )
    assert result.success
    assert "Findings here" in result.data["content"]
