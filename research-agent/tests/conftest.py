"""Pytest fixtures for research-agent tests."""
import os
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def temp_data_dir(monkeypatch):
    """Redirect data dir to temp for tests."""
    # Detached GC is exercised explicitly; ordinary tests must not leave workers
    # touching temporary databases after fixture teardown.
    monkeypatch.setattr("research_agent.git_maintenance.launch_worker", lambda filename: None)
    # Clear the episodic-store in-memory cache (DB path changes per test).
    try:
        from research_agent import plan
        plan.reset_for_tests()
    except Exception:
        pass
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("RESEARCH_AGENT_DATA_DIR", tmpdir)
        yield Path(tmpdir)
        # Let async episode writes finish before the temp dir is removed.
        try:
            from research_agent import plan_pipeline
            plan_pipeline.drain()
        except Exception:
            pass
        try:
            from research_agent import plan
            plan.reset_for_tests()
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
