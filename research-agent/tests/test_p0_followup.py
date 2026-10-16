import os
import subprocess
import sys
import threading
import pytest
from research_agent.execution import exclusive_request, CancellationToken, Cancelled
from research_agent.process_control import run_local


def test_background_lease_keeps_workspace_busy_after_request(tmp_path):
    with exclusive_request(str(tmp_path), "chat") as lease:
        release = lease.retain()
    with pytest.raises(RuntimeError):
        with exclusive_request(str(tmp_path), "other"):
            pass
    release()
    with exclusive_request(str(tmp_path), "chat"):
        pass


def test_running_local_command_is_cancelled(tmp_path):
    token = CancellationToken()
    args = [sys.executable, "-c", "import time; time.sleep(30)"]
    if os.name == "nt":
        command = subprocess.list2cmdline(args)
    else:
        import shlex
        command = shlex.join(args)
    timer = threading.Timer(0.3, token.cancel)
    timer.start()
    try:
        with pytest.raises(Cancelled):
            run_local(command, str(tmp_path), 10, token)
    finally:
        timer.join()


def test_completed_turn_is_saved_only_once(temp_workspace):
    from research_agent.project_manager import save_turn, get_recent_turns
    workspace, chat = temp_workspace
    save_turn(workspace, chat, 1, "input", "answer", execution_id="same")
    save_turn(workspace, chat, 2, "input", "answer", execution_id="same")
    assert len(get_recent_turns(workspace, chat)) == 1
