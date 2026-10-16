from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from research_agent.plugins import ResourceScope
from research_agent.service_bindings import ServiceRouter, SessionBinding


def test_replace_pins_old_call_and_routes_new_call():
    router = ServiceRouter()
    old = ResourceScope("old")
    cleaned = []
    old.own(lambda: cleaned.append("old"))
    router.publish("model", "old-model", old)
    with router.borrow("model") as service:
        router.publish("model", "new-model", ResourceScope("new"))
        assert service == "old-model"
        with router.borrow("model") as new:
            assert new == "new-model"
        with pytest.raises(TimeoutError):
            router.reap(timeout=0)
        assert cleaned == []
    router.reap(timeout=0)
    assert cleaned == ["old"]


def test_global_unload_waits_and_blocks_new_calls():
    router = ServiceRouter()
    scope = ResourceScope("plugin")
    router.publish("tool", object(), scope)
    started, release = Event(), Event()
    def use():
        with router.borrow("tool"):
            started.set()
            assert release.wait(5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(use)
        assert started.wait(5)
        unloading = pool.submit(router.remove, "tool")
        with scope._condition:
            assert scope._condition.wait_for(lambda: scope.draining, 5)
        assert not unloading.done()
        with pytest.raises(KeyError):
            with router.borrow("tool"):
                pass
        release.set()
        running.result(5)
        unloading.result(5)
    assert scope.closed


def test_session_stop_does_not_stop_other_window():
    router = ServiceRouter()
    scope = ResourceScope("application")
    router.publish("tool", "shared", scope)
    first, second = SessionBinding("a", router), SessionBinding("b", router)
    first.close()
    with pytest.raises(RuntimeError):
        with first.borrow("tool"):
            pass
    with second.borrow("tool") as service:
        assert service == "shared"
    assert not scope.closed
