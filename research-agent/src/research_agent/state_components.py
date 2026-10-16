"""Injectable state component declarations; no tool or kernel dependencies."""
from dataclasses import dataclass
from typing import Any, Callable
from threading import RLock
from copy import deepcopy


def synchronized(method):
    from functools import wraps
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


@dataclass(frozen=True)
class StateComponent:
    key: str
    factory: Callable[[], Any]
    cleanup: Callable[[Any], None] | None = None
    validate: Callable[[Any], None] | None = None
    version: int = 1
    encode: Callable[[Any], Any] | None = None
    decode: Callable[[Any], Any] | None = None
    migrate: Callable[[Any, int], Any] | None = None
    owner: str = ""
    lifetime: str = "request"
    value_type: type | tuple[type, ...] | None = None
    copy_on_read: bool = False
    fork: Callable[[Any], Any] | None = None

    def __post_init__(self):
        if self.version < 1 or self.lifetime not in ("application", "session", "task", "request"):
            raise ValueError("Invalid state version/lifetime")
        if (self.encode is None) != (self.decode is None):
            raise ValueError("Persistent fields require both encode and decode")

    def check(self, value):
        if self.value_type is not None:
            if not isinstance(value, self.value_type) or (self.value_type is int and isinstance(value, bool)):
                raise TypeError(f"Invalid state type: {self.key}")
        if self.validate:
            self.validate(value)


class ComponentStore:
    """Per-state instances; declarations can be added and removed at runtime."""
    def __init__(self):
        self._values: dict[str, Any] = {}
        self._specs: dict[str, StateComponent] = {}
        self._revisions = {}
        self._lock = RLock()

    @synchronized
    def inject(self, spec: StateComponent) -> Any:
        if spec.key in self._specs:
            if self._specs[spec.key] != spec:
                raise ValueError(f"Component already registered: {spec.key}")
            return self._values[spec.key]
        value = spec.factory()
        spec.check(value)
        self._values[spec.key] = value
        self._specs[spec.key] = spec
        return value

    @synchronized
    def get(self, key: str) -> Any:
        value = self._values[key]
        return deepcopy(value) if self._specs[key].copy_on_read else value

    @synchronized
    def set(self, key: str, value: Any) -> None:
        if key not in self._specs:
            raise KeyError(key)
        self._specs[key].check(value)
        self._values[key] = deepcopy(value) if self._specs[key].copy_on_read else value

    def update(self, key, transform, *, owner):
        with self._lock:
            if self._specs[key].owner != owner:
                raise PermissionError(f"State field not owned by {owner}: {key}")
            value = transform(deepcopy(self._values[key]))
            self.set(key, value)
            return self.get(key)

    def view(self, owner):
        return StateView(self, owner)

    @synchronized
    def reset(self, *, lifetimes, exclude_owners=()):
        """Host boundary: recreate matching fields and release their old resources."""
        staged = {}
        for key, spec in self._specs.items():
            if spec.lifetime in lifetimes and spec.owner not in exclude_owners:
                value = spec.factory()
                spec.check(value)
                staged[key] = value
        for key, value in staged.items():
            spec = self._specs[key]
            if spec.cleanup:
                spec.cleanup(self._values[key])
            self._values[key] = value

    @synchronized
    def save(self, path, *, lifetimes=None):
        from research_agent.execution import exclusive_request
        from pathlib import Path
        import hashlib
        path = Path(path).resolve()
        with exclusive_request(str(path), "snapshot:" + str(path)):
            current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            if str(path) in self._revisions and self._revisions[str(path)] != current:
                raise RuntimeError("State snapshot conflict: reload before saving")
            self._save(path, lifetimes=lifetimes)
            self._revisions[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()

    @synchronized
    def snapshot(self, *, lifetimes=None):
        """Export declared data only; live resources never have a codec."""
        snapshot = {}
        for key, spec in self._specs.items():
            if spec.encode is None or (lifetimes is not None and spec.lifetime not in lifetimes):
                continue
            spec.check(self._values[key])
            snapshot[key] = {"version": spec.version, "owner": spec.owner,
                             "lifetime": spec.lifetime, "value": deepcopy(spec.encode(self._values[key]))}
        return snapshot

    def _save(self, path, *, lifetimes=None):
        import json, os, tempfile
        from pathlib import Path
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Preserve unloaded plugin data; unloading is not deletion of user data.
        snapshot = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        for key, spec in self._specs.items():
            spec.check(self._values[key])
        snapshot.update(self.snapshot(lifetimes=lifetimes))
        descriptor, temporary = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(snapshot, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @synchronized
    def restore(self, path, *, lifetimes=None):
        import json
        from pathlib import Path
        import hashlib
        raw = Path(path).read_bytes()
        snapshot = json.loads(raw)
        self.restore_snapshot(snapshot, lifetimes=lifetimes)
        self._revisions[str(Path(path).resolve())] = hashlib.sha256(raw).hexdigest()

    @synchronized
    def restore_snapshot(self, snapshot, *, lifetimes=None, strict=False):
        """Validate every loaded field before committing any restored value."""
        if strict:
            missing = [key for key in snapshot
                       if key not in self._specs or self._specs[key].decode is None]
            if missing:
                raise ValueError(f"Unavailable execution state fields: {sorted(missing)}")
        staged = {}
        for key, spec in self._specs.items():
            if key not in snapshot or spec.decode is None or (lifetimes is not None and spec.lifetime not in lifetimes):
                continue
            item = snapshot[key]
            if item.get("owner", spec.owner) != spec.owner or item.get("lifetime", spec.lifetime) != spec.lifetime:
                raise ValueError(f"State owner/lifetime mismatch: {key}")
            value = deepcopy(item["value"])
            if item["version"] != spec.version:
                if spec.migrate is None:
                    raise ValueError(f"Missing migration: {key}")
                value = spec.migrate(value, item["version"])
            value = spec.decode(value)
            spec.check(value)
            staged[key] = value
        self._values.update(staged)

    @synchronized
    def remove(self, key: str) -> bool:
        if key not in self._specs:
            return False
        spec = self._specs[key]
        if spec.cleanup:
            spec.cleanup(self._values[key])
        del self._specs[key]
        del self._values[key]
        return True

    @synchronized
    def keys(self) -> tuple[str, ...]:
        return tuple(self._values)


class StateView:
    """Owner-scoped updates and detached reads. Host keeps administrative access."""
    def __init__(self, store, owner):
        self._store, self.owner = store, owner

    def _check(self, key):
        if self._store._specs[key].owner != self.owner:
            raise PermissionError(f"Field not owned by {self.owner}: {key}")

    def get(self, key):
        with self._store._lock:
            self._check(key)
            return deepcopy(self._store._values[key])

    def set(self, key, value):
        with self._store._lock:
            self._check(key)
            self._store.set(key, deepcopy(value))

    def update(self, key, transform):
        return deepcopy(self._store.update(key, transform, owner=self.owner))
