import pytest
from research_agent.plugins import PluginManager, PluginDeclaration, ResourceScope, Lifetime


def test_dependency_start_stop_restart_and_borrowed_resource():
    events = []
    manager = PluginManager()
    def consumer(ctx):
        assert ctx.services["model"] == "client"
        events.append("consumer-start")
        ctx.scope.own(lambda: events.append("consumer-stop"))
    def provider(ctx):
        ctx.provide("model", "client")
        ctx.scope.own(lambda: events.append("provider-stop"))
    manager.install(PluginDeclaration("consumer", consumer, requires=("model",)))
    assert manager.phases["consumer"] == "pending"
    manager.install(PluginDeclaration("provider", provider, provides=("model",)))
    assert manager.phases["consumer"] == "active"
    manager.disable("provider")
    assert events[-2:] == ["consumer-stop", "provider-stop"]
    assert manager.phases["consumer"] == "pending"
    manager.enable("provider")
    assert manager.phases["consumer"] == "active"


def test_apply_failure_rolls_back_owned_resources():
    events = []
    def apply(ctx):
        ctx.scope.own(lambda: events.append("released"))
        raise ValueError("failed")
    manager = PluginManager()
    with pytest.raises(ValueError):
        manager.install(PluginDeclaration("bad", apply))
    assert events == ["released"]
    assert manager.services == {}
    assert manager.phases["bad"] == "failed"


def test_nested_scopes_cleanup_reverse_and_idempotent():
    events = []
    scope = ResourceScope("test")
    scope.own(lambda: events.append("parent"))
    scope.child(Lifetime.REQUEST).own(lambda: events.append("request"))
    scope.close()
    scope.close()
    assert events == ["request", "parent"]
