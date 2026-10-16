import asyncio
import pytest
from research_agent.evidence import publish, ingest_once, read, database_path
from research_agent.state_components import ComponentStore, StateComponent
from research_agent.plugins import ResourceScope


def test_outbox_survives_missing_collector_and_replays_idempotently():
    for sequence, kind in enumerate(("created", "drain", "cleanup", "closed"), 1):
        publish(dict(scope="test", sequence=sequence, kind=kind, owner="test", lifetime="application"))
    path = database_path()
    assert read(path)["evidence_health"]["pending"] == 4
    import subprocess, sys
    result = subprocess.run([sys.executable, "-m", "research_agent.evidence", "--database", str(path), "--once"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert ingest_once(path) == 0
    assert read(path)["evidence_health"] == {"pending": 0, "incomplete": 0, "complete": 1}


def test_state_save_restore_validate_migrate_and_unload(tmp_path):
    path = tmp_path / "state.json"
    store = ComponentStore()
    store.inject(StateComponent("task", dict, encode=dict, decode=dict))
    store.set("task", {"done": False})
    store.save(path)
    store.remove("task")
    cleaned = []
    store.inject(StateComponent("task", dict, cleanup=cleaned.append, version=2,
        encode=dict, decode=dict, migrate=lambda value, version: {**value, "version": 2}))
    store.restore(path)
    assert store.get("task") == {"done": False, "version": 2}
    store.remove("task")
    assert len(cleaned) == 1


def test_async_cleanup_finishes_before_scope_closed():
    events = []
    scope = ResourceScope("async")
    async def cleanup():
        await asyncio.sleep(0)
        events.append("done")
    scope.own_async(cleanup)
    asyncio.run(scope.aclose())
    assert scope.closed and events == ["done"]
