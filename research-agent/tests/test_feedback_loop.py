import tempfile
import os

from research_agent.agent import _auto_validate
from research_agent.models import AgentState


def test_syntax_error_detected():
    with tempfile.TemporaryDirectory() as tmpdir:
        py_file = os.path.join(tmpdir, "broken.py")
        with open(py_file, "w", encoding="utf-8") as f:
            f.write("def foo():\n    print('missing colon'\n")

        state = AgentState(workspace_dir=tmpdir)
        messages = []
        events = []

        def on_event(evt_type, data):
            events.append((evt_type, data))

        _auto_validate(state, "file_write", {"path": "broken.py"}, messages, on_event)

        error_msgs = [m["content"] for m in messages
                      if m["role"] == "system" and "检查失败" in m["content"]]
        assert len(error_msgs) > 0, f"Expected error message, got messages: {messages}"
        assert "syntax" in error_msgs[0].lower() or "error" in error_msgs[0].lower()


def test_valid_file_passes():
    with tempfile.TemporaryDirectory() as tmpdir:
        py_file = os.path.join(tmpdir, "valid.py")
        with open(py_file, "w", encoding="utf-8") as f:
            f.write("def add(a, b):\n    return a + b\n")

        state = AgentState(workspace_dir=tmpdir)
        messages = []

        def on_event(evt_type, data):
            pass

        _auto_validate(state, "file_write", {"path": "valid.py"}, messages, on_event)

        error_msgs = [m for m in messages
                      if m["role"] == "system" and "检查失败" in m.get("content", "")]
        assert len(error_msgs) == 0, f"Expected no error, got: {error_msgs}"


def test_non_py_skipped():
    with tempfile.TemporaryDirectory() as tmpdir:
        txt_file = os.path.join(tmpdir, "notes.txt")
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write("some notes")

        state = AgentState(workspace_dir=tmpdir)
        messages = []

        def on_event(evt_type, data):
            pass

        _auto_validate(state, "file_write", {"path": "notes.txt"}, messages, on_event)

        assert len(messages) == 0, f"Expected no messages, got: {messages}"
