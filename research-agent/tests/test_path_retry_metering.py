from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from research_agent.path_policy import safe_path, safe_glob
from research_agent.guardrail import validate_path
from research_agent.model_retry import stream_with_retry, retry_model, PartialResponseError, ModelRequestRejected


def test_sibling_prefix_and_glob_escape_are_rejected(tmp_path):
    root = tmp_path / "workspace"
    outside = tmp_path / "workspace-other"
    root.mkdir(); outside.mkdir()
    (outside / "private.txt").write_text("private")
    assert safe_path(root, "../workspace-other/private.txt") is None
    assert validate_path("../workspace-other/private.txt", str(root)) is not None
    with pytest.raises(ValueError):
        list(safe_glob(root, "../workspace-other/*"))
    assert safe_path(root, "new/nested.txt") == str(root / "new" / "nested.txt")


def test_links_cannot_escape_read_write_or_search(tmp_path):
    root, outside = tmp_path / "ws", tmp_path / "external"
    root.mkdir(); outside.mkdir()
    (outside / "private.txt").write_text("secret")
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        import os
        if os.name != "nt":
            pytest.skip("Symlink creation unavailable")
        import _winapi
        _winapi.CreateJunction(str(outside), str(root / "linked"))
    assert safe_path(root, "linked/private.txt") is None
    assert safe_path(root, "linked/new.txt") is None
    assert list(safe_glob(root, "**/*")) == []


def test_safe_search_preserves_recursive_matching(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "nested" / "b.py").write_text("x")
    assert set(safe_glob(tmp_path, "**/*.py")) == {"a.py", "nested/b.py"}
    assert list(safe_glob(tmp_path, "*.py")) == ["a.py"]


def test_partial_stream_is_published_once_without_retry():
    calls, events = [], []
    def invoke(emit):
        calls.append(1)
        emit("reply", {"text": "partial"})
        raise TimeoutError("stream lost")
    with pytest.raises(PartialResponseError) as failure:
        stream_with_retry(invoke, lambda kind, data: events.append((kind, data)))
    assert failure.value.partial == "partial"
    assert len(calls) == 1 and len(events) == 1


def test_transient_before_publication_retries_but_permanent_errors_stop(monkeypatch):
    monkeypatch.setattr("research_agent.model_retry.time.sleep", lambda seconds: None)
    calls = []
    def invoke(emit):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError()
        return "ok"
    assert stream_with_retry(invoke, None) == "ok"
    assert len(calls) == 2
    class Unauthorized(Exception):
        status_code = 401
    calls.clear()
    def fail():
        calls.append(1)
        raise Unauthorized("invalid credentials")
    with pytest.raises(ModelRequestRejected):
        retry_model(fail, None)
    assert calls == [1]


def test_metered_adapter_counts_select_and_answer_once_and_restores_context():
    from research_agent import telemetry
    from research_agent.runtime import RuntimeContext
    from research_agent.models import AgentState
    class Adapter:
        def select(self, *args):
            return {"usage": {"prompt_tokens": 10, "completion_tokens": 2}}
        def answer(self, messages, emit):
            emit("llm_usage", {"model": "deepseek-chat", "prompt_tokens": 20, "completion_tokens": 3})
            return "done"
    adapter = Adapter()
    ctx = RuntimeContext(SimpleNamespace(model="deepseek-chat"), "task", AgentState(), chat_id="adapter", model_adapter=adapter)
    def run(ctx):
        ctx.model_adapter.select([], [])
        ctx.state.final_response = ctx.model_adapter.answer([], ctx.emit)
    telemetry.MeteredRuntime(SimpleNamespace(run=run)).run(ctx)
    result = telemetry.query_run("adapter")
    assert result["tokens"]["total"] == 35
    assert len(result["llm_calls"]) == 2 and ctx.model_adapter is adapter


def test_concurrent_telemetry_runs_do_not_mix_sessions():
    from research_agent import telemetry
    barrier = Barrier(2)
    def worker(name, tokens):
        telemetry.begin_run(name)
        barrier.wait(timeout=5)
        telemetry.note_llm_call("deepseek-chat", {"prompt_tokens": tokens, "completion_tokens": 1}, 1)
        barrier.wait(timeout=5)
        return telemetry.end_run("done")
    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = pool.submit(worker, "one", 10), pool.submit(worker, "two", 20)
        a, b = one.result(5), two.result(5)
    assert (a["trace"], a["tokens"]["total"]) == ("one", 11)
    assert (b["trace"], b["tokens"]["total"]) == ("two", 21)
