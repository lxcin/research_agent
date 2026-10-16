from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from research_agent import plan_pipeline, episode_jobs
from research_agent.execution import CancellationToken, Cancelled, cancellable_call, exclusive_request
from research_agent.models import AgentState


def test_pause_waits_for_current_job_and_leaves_new_jobs_durable(monkeypatch, tmp_path):
    entered, release = Event(), Event()
    plan_pipeline.resume()
    original = plan_pipeline._process
    def slow(job):
        entered.set()
        assert release.wait(5)
        original(job)
    monkeypatch.setattr(plan_pipeline, "_process", slow)
    try:
        plan_pipeline.submit_run(user_input="first", workspace_dir=str(tmp_path), execution_id="one", use_llm=False)
        assert entered.wait(5)
        with pytest.raises(TimeoutError):
            plan_pipeline.pause(timeout=0)
        plan_pipeline.submit_run(user_input="second", workspace_dir=str(tmp_path), execution_id="two", use_llm=False)
        release.set()
        plan_pipeline.drain()
        assert [key for _, key in episode_jobs.pending()] == ["two"]
        plan_pipeline.resume()
        plan_pipeline.recover_pending()
        plan_pipeline.drain()
        assert not episode_jobs.pending()
    finally:
        release.set()
        plan_pipeline.resume()
        plan_pipeline.drain()


def test_blocked_model_cancel_retains_request_until_transport_exits(tmp_path):
    entered, release = Event(), Event()
    token = CancellationToken()
    state = AgentState()
    state._cancellation = token
    def blocked():
        entered.set()
        release.wait(5)
        return "late result"
    with ThreadPoolExecutor(max_workers=1) as pool:
        with exclusive_request(str(tmp_path), "chat") as lease:
            state._request_lease = lease
            future = pool.submit(cancellable_call, blocked, state)
            assert entered.wait(5)
            token.cancel()
            with pytest.raises(Cancelled):
                future.result(2)
        try:
            with pytest.raises(RuntimeError):
                with exclusive_request(str(tmp_path), "new"):
                    pass
        finally:
            release.set()


def test_docker_stop_failure_quarantines_workspace(monkeypatch, tmp_path):
    from research_agent.process_control import stop_container
    from types import SimpleNamespace
    monkeypatch.setattr("research_agent.process_control.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=1, stderr="daemon unavailable"))
    with pytest.raises(RuntimeError):
        stop_container("docker", "owned-container", str(tmp_path))
    with pytest.raises(RuntimeError, match="quarantined"):
        with exclusive_request(str(tmp_path), "new"):
            pass
