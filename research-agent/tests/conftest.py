"""Pytest fixtures for research-agent tests."""
import os
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def temp_data_dir(monkeypatch):
    """Redirect data dir to temp for tests. Reset global memory state."""
    # Reset Tier B memory subsystem (SQLite conn + vector availability).
    try:
        from research_agent.memory.tier_b import reset_for_tests
        reset_for_tests()
    except Exception:
        pass

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("RESEARCH_AGENT_DATA_DIR", tmpdir)
        yield Path(tmpdir)
        # Close Tier B memory SQLite connection so temp dir can be removed.
        try:
            from research_agent.memory.tier_b import reset_for_tests
            reset_for_tests()
        except Exception:
            pass


@pytest.fixture
def temp_workspace(temp_data_dir):
    """Create a temp workspace directory with initialized project and chat."""
    from research_agent import project_manager
    ws = tempfile.mkdtemp()
    project_manager.init_project(ws, topic="test")
    chat_id = project_manager.create_chat(ws, title="test chat")
    yield ws, chat_id
    shutil.rmtree(ws, ignore_errors=True)