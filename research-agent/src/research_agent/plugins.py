"""Dependency-driven plugin activation and owned, synchronous resource scopes."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Any
from threading import Condition, Lock, RLock
from contextlib import contextmanager


class Lifetime(str, Enum):
    APPLICATION = "application"
    SESSION = "session"
    TASK = "task"
    REQUEST = "request"


class ResourceScope:
    def __init__(self, owner: str, lifetime: Lifetime = Lifetime.APPLICATION):
        self.owner, self.lifetime = owner, lifetime
        self._cleanup: list[Callable[[], None]] = []
        self._drain_hooks = []
        self.closed = False
        from research_agent.evidence import EvidenceEmitter
        self._evidence = EvidenceEmitter(owner, lifetime.value)
        self._evidence.emit("created")
        self.draining = False
        self.active_calls = 0
        self._condition = Condition()
        self._close_lock = Lock()

    def own(self, cleanup: Callable[[], None]) -> None:
        with self._condition:
            if self.closed or self.draining:
                raise RuntimeError("scope is closed")
            self._cleanup.append(cleanup)

    def own_async(self, cleanup):
        import asyncio
        self.own(lambda: asyncio.run(cleanup()))

    def on_drain(self, stop_admission):
        with self._condition:
            if self.draining:
                raise RuntimeError("Scope already draining")
            self._drain_hooks.append(stop_admission)

    async def aclose(self, timeout=None):
        import asyncio
        await asyncio.to_thread(self.close, timeout)

    def child(self, lifetime: Lifetime):
        child = ResourceScope(self.owner, lifetime)
        self.own(child.close)
        return child

    @contextmanager
    def acquire(self):
        with self._condition:
            if self.draining or self.closed:
                raise RuntimeError("scope is draining or closed")
            self.active_calls += 1
            self._evidence.emit("acquire")
        try:
            yield self
        finally:
            with self._condition:
                self.active_calls -= 1
                self._evidence.emit("release")
                self._condition.notify_all()

    def drain(self, timeout: float | None = None):
        with self._condition:
            self.draining = True
            self._evidence.emit("drain")
            self._condition.notify_all()
        for hook in self._drain_hooks:
            hook()
        with self._condition:
            if not self._condition.wait_for(lambda: self.active_calls == 0, timeout):
                raise TimeoutError(f"Waiting for {self.active_calls} calls: {self.owner}")

    def close(self, timeout: float | None = None) -> None:
        with self._condition:
            if self.closed:
                return
        self.drain(timeout)
        with self._close_lock:
            self._close_resources()

    def _close_resources(self) -> None:
        if self.closed:
            return
        self._evidence.emit("cleanup")
        errors = []
        failed = []
        for cleanup in reversed(self._cleanup):
            try:
                cleanup()
            except Exception as exc:
                self._evidence.emit("cleanup_error")
                errors.append(exc)
                failed.append(cleanup)
        self._cleanup = list(reversed(failed))
        self.closed = not failed
        if self.closed:
            self._evidence.emit("closed")
        if errors:
            raise ExceptionGroup(f"Cleanup failed: {self.owner}", errors)


@dataclass(frozen=True)
class PluginDeclaration:
    id: str
    apply: Callable[["PluginContext"], None]
    requires: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()


@dataclass
class PluginContext:
    declaration: PluginDeclaration
    scope: ResourceScope
    services: dict[str, Any]
    _published: dict[str, Any] = field(default_factory=dict)

    def provide(self, name: str, service: Any) -> None:
        if name not in self.declaration.provides:
            raise ValueError(f"Undeclared service: {name}")
        if name in self._published:
            raise ValueError(f"Duplicate service: {name}")
        self._published[name] = service

    def register_tool(self, registry, tool) -> None:
        if tool.name in registry:
            raise ValueError(f"Tool already registered: {tool.name}")
        from dataclasses import replace
        handler = tool.handler
        def guarded(*args, **kwargs):
            with self.scope.acquire():
                return handler(*args, **kwargs)
        registry.register(replace(tool, handler=guarded))
        self.scope.own(lambda: registry.unregister(tool.name))

    def inject_field(self, state, spec) -> Any:
        from dataclasses import replace
        if spec.owner and spec.owner != self.declaration.id:
            raise ValueError("Plugin cannot declare another owner state")
        spec = replace(spec, owner=self.declaration.id)
        if not spec.key.startswith(self.declaration.id + "."):
            raise ValueError("State field must be namespaced by plugin id")
        if spec.key in state.components.keys():
            raise ValueError(f"Field already registered: {spec.key}")
        value = state.components.inject(spec)
        self.scope.own(lambda: state.components.remove(spec.key))
        return value


def serialized(method):
    from functools import wraps
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._control_lock:
            return method(self, *args, **kwargs)
    return call


class PluginManager:
    """Control-plane manager; mutate only outside active request execution."""
    def __init__(self):
        self.declarations: dict[str, PluginDeclaration] = {}
        self.contexts: dict[str, PluginContext] = {}
        self.services: dict[str, Any] = {}
        self.phases: dict[str, str] = {}
        self.enabled: set[str] = set()
        self._control_lock = RLock()

    @serialized
    def install(self, declaration: PluginDeclaration) -> None:
        if declaration.id in self.declarations:
            raise ValueError(f"Plugin already installed: {declaration.id}")
        claimed = {name for item in self.declarations.values() for name in item.provides}
        if claimed.intersection(declaration.provides):
            raise ValueError("Service provider collision")
        self.declarations[declaration.id] = declaration
        self.enabled.add(declaration.id)
        self.phases[declaration.id] = "pending"
        self.start()

    @serialized
    def start(self) -> None:
        progress = True
        while progress:
            progress = False
            for key, declaration in self.declarations.items():
                if key not in self.enabled or self.phases[key] != "pending":
                    continue
                if not all(name in self.services for name in declaration.requires):
                    continue
                self.phases[key] = "loading"
                names = declaration.requires + declaration.optional
                ctx = PluginContext(declaration, ResourceScope(key),
                                    {name: self.services[name] for name in names if name in self.services})
                try:
                    declaration.apply(ctx)
                    if set(ctx._published) != set(declaration.provides):
                        raise ValueError("Plugin did not provide all declared services")
                except Exception:
                    self.phases[key] = "failed"
                    self.contexts[key] = ctx
                    ctx.scope.close()
                    raise
                self.contexts[key] = ctx
                self.services.update(ctx._published)
                self.phases[key] = "active"
                progress = True

    @serialized
    def disable(self, key: str) -> None:
        self.enabled.discard(key)
        self._stop(key, set())

    def _stop(self, key: str, visiting: set[str]) -> None:
        if key in visiting:
            return
        visiting.add(key)
        declaration = self.declarations[key]
        for other, ctx in list(self.contexts.items()):
            if other != key and set(ctx.declaration.requires).intersection(declaration.provides):
                self._stop(other, visiting)
        ctx = self.contexts.get(key)
        if ctx:
            self.phases[key] = "unloading"
            ctx.scope.close()
            for name in ctx._published:
                self.services.pop(name, None)
            self.contexts.pop(key)
        self.phases[key] = "pending" if key in self.enabled else "disposed"

    @serialized
    def enable(self, key: str) -> None:
        if key in self.contexts:
            if self.phases[key] != "active":
                raise RuntimeError("Complete failed cleanup before enabling")
            return
        self.enabled.add(key)
        self.phases[key] = "pending"
        self.start()

    @serialized
    def uninstall(self, key: str) -> None:
        self.disable(key)
        del self.declarations[key]
        del self.phases[key]
