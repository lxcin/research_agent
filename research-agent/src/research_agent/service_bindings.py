"""Versioned service routing and independent session admission scopes."""
from contextlib import contextmanager, ExitStack
from threading import RLock
from research_agent.plugins import ResourceScope, Lifetime


class ServiceRouter:
    """Publish new versions atomically; callers pin a version for their lease."""
    def __init__(self):
        self._lock = RLock()
        self._versions = {}
        self._retired = []

    def publish(self, name, service, scope: ResourceScope):
        with self._lock:
            old = self._versions.get(name)
            if old is not None and old[1] is scope:
                raise ValueError("Replacement requires a distinct scope")
            self._versions[name] = (service, scope)
            if old:
                self._retired.append(old[1])
            return old[1] if old else None

    @contextmanager
    def borrow(self, name):
        with ExitStack() as stack:
            with self._lock:
                service, scope = self._versions[name]
                stack.enter_context(scope.acquire())
            yield service

    def remove(self, name, timeout=None):
        with self._lock:
            old = self._versions.pop(name, None)
            if old:
                self._retired.append(old[1])
        if old:
            old[1].close(timeout)
            with self._lock:
                self._retired.remove(old[1])

    def reap(self, timeout=None):
        with self._lock:
            retired = list(self._retired)
        for scope in retired:
            scope.close(timeout)
            with self._lock:
                if scope in self._retired:
                    self._retired.remove(scope)


class SessionBinding:
    """Stopping one window does not stop its shared application service."""
    def __init__(self, session_id, router: ServiceRouter):
        self.router = router
        self.scope = ResourceScope(session_id, Lifetime.SESSION)

    @contextmanager
    def borrow(self, name):
        with self.scope.acquire(), self.router.borrow(name) as service:
            yield service

    def close(self, timeout=None):
        self.scope.close(timeout)
