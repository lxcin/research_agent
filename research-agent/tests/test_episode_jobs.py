import json
import os
import subprocess
import sys
import pytest
from research_agent import episode_jobs, plan_pipeline, plan


def test_crashed_worker_is_reclaimed_and_completed_job_is_not_repeated(tmp_path):
    payload = {"digest": {"user_input": "test"}, "workspace_dir": str(tmp_path), "chat_id": "chat", "use_llm": False}
    filename, key = episode_jobs.enqueue("crash-job", payload)
    script = "from research_agent.episode_jobs import process; import os,sys; process(sys.argv[1],sys.argv[2],lambda payload: os._exit(7))"
    result = subprocess.run([sys.executable, "-c", script, str(filename), key], timeout=10)
    assert result.returncode == 7
    calls = []
    episode_jobs.process(filename, key, calls.append)
    episode_jobs.process(filename, key, calls.append)
    assert calls == [payload]
    assert episode_jobs.pending() == []


def test_failed_job_is_visible_and_retry_is_explicit():
    filename, key = episode_jobs.enqueue("failed", {"input": "test"})
    def fail(payload):
        raise ValueError("storage unavailable")
    with pytest.raises(ValueError):
        episode_jobs.process(filename, key, fail)
    assert episode_jobs.pending() == []
    assert episode_jobs.pending(retry_failed=True) == [(filename, key)]
    recovered = []
    episode_jobs.process(filename, key, recovered.append)
    assert recovered == []
    episode_jobs.process(filename, key, recovered.append, retry_failed=True)
    assert recovered == [{"input": "test"}]
    with pytest.raises(ValueError):
        episode_jobs.enqueue(key, {"input": "different"})


def test_pipeline_recovers_persisted_job_without_live_model(tmp_path):
    payload = {"digest": {"execution_id": "recover", "user_input": "saved task",
                           "final_response": "done", "tool_path": ["file_read"]},
               "workspace_dir": str(tmp_path), "chat_id": "c", "use_llm": True}
    episode_jobs.enqueue("recover", payload)
    assert plan_pipeline.recover_pending() == 1
    plan_pipeline.drain()
    assert episode_jobs.pending() == []
    plans = plan.list_plans(str(tmp_path))
    assert len(plans) == 1
    assert plan_pipeline.recover_pending() == 0
