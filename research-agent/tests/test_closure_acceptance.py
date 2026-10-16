import json
import pytest
from research_agent.plugins import ResourceScope
from research_agent.evidence import database_path, ingest_once, read, publish
from research_agent.state_components import ComponentStore, StateComponent


def test_timeout_keeps_resources_then_retry_cleans_once():
    scope = ResourceScope("timeout")
    cleaned = []
    scope.own(lambda: cleaned.append(True))
    with scope.acquire():
        with pytest.raises(TimeoutError):
            scope.close(timeout=0)
        assert not scope.closed and cleaned == []
        with pytest.raises(RuntimeError):
            with scope.acquire():
                pass
    scope.close()
    scope.close()
    assert cleaned == [True]
    ingest_once(database_path())
    assert read(database_path())["evidence_health"]["complete"] == 1


def test_cleanup_failure_retains_only_failed_disposer():
    scope = ResourceScope("retry")
    attempts, good = [], []
    def fail_once():
        attempts.append(True)
        if len(attempts) == 1:
            raise ValueError("retry")
    scope.own(lambda: good.append(True))
    scope.own(fail_once)
    with pytest.raises(ExceptionGroup):
        scope.close()
    assert not scope.closed
    scope.close()
    assert scope.closed and len(attempts) == 2 and len(good) == 1


def test_failed_restore_is_atomic_and_failed_save_preserves_file(tmp_path):
    store = ComponentStore()
    store.inject(StateComponent("a", lambda: 1, encode=lambda value: value, decode=int))
    def reject(value):
        if value < 0:
            raise ValueError("negative")
    store.inject(StateComponent("b", lambda: 2, validate=reject,
                                encode=lambda value: value, decode=int))
    path = tmp_path / "state.json"
    store.save(path)
    original = path.read_bytes()
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"a": {"version": 1, "value": 99},
                               "b": {"version": 1, "value": -1}}))
    with pytest.raises(ValueError):
        store.restore(bad)
    assert store.get("a") == 1
    store.set("a", object())
    with pytest.raises(TypeError):
        store.save(path)
    assert path.read_bytes() == original


def test_unloaded_field_survives_subsequent_snapshot(tmp_path):
    store = ComponentStore()
    spec = StateComponent("plugin.data", dict, encode=dict, decode=dict)
    store.inject(spec)
    store.set(spec.key, {"value": 7})
    path = tmp_path / "state.json"
    store.save(path)
    store.remove(spec.key)
    store.save(path)
    store.inject(spec)
    store.restore(path)
    assert store.get(spec.key) == {"value": 7}


def test_missing_event_is_reported_not_accepted():
    for sequence, kind in ((1, "created"), (3, "closed")):
        publish(dict(scope="gap", sequence=sequence, kind=kind, owner="test", lifetime="task"))
    ingest_once(database_path())
    result = read(database_path())
    assert result["resource_scopes"] == []
    assert result["evidence_health"]["incomplete"] == 1
