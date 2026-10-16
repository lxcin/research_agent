"""Docker resource ownership follows OS-held capacity leases, not PID guesses.

Only a free slot can be reconciled. Its deterministic container name prevents a
late daemon create from overlapping a replacement after the original host dies.
Labels scope cleanup to this installation; legacy/unlabelled containers are ignored.
"""
from dataclasses import dataclass
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

PREFIX = "io.research-agent."


def scope_id():
    from research_agent.config import get_data_dir
    return hashlib.sha256(os.path.normcase(os.path.realpath(get_data_dir())).encode()).hexdigest()[:24]


def _run(cfg, *args):
    result = subprocess.run([cfg.docker_bin, *args], capture_output=True,
                            text=True, errors="replace", timeout=30)
    if result.returncode:
        raise RuntimeError("Docker reconciliation failed: " + getattr(result, "stderr", ""))
    return result.stdout.strip()


def _inspect(cfg, container_id):
    result = subprocess.run([cfg.docker_bin, "inspect", container_id], capture_output=True,
                            text=True, errors="replace", timeout=30)
    if result.returncode:
        error = result.stderr.lower()
        if container_id in result.stderr and ("no such object" in error or "no such container" in error):
            return None  # --rm may have completed since the preceding list.
        raise RuntimeError("Docker inspection failed: " + result.stderr)
    return json.loads(result.stdout)[0]


def _lease(index):
    from research_agent.config import get_data_dir
    from research_agent.execution import exclusive_request
    return exclusive_request(str(get_data_dir() / "docker_slots" / str(index)), "docker-slot:" + str(index))


@dataclass
class DockerSlot:
    index: int
    scope: str
    lease: object
    root: Path | None = None

    def __post_init__(self):
        if self.root is None:
            from research_agent.config import get_data_dir
            self.root = get_data_dir().resolve()

    @property
    def name(self):
        return f"ra-{self.scope}-{self.index}"

    @property
    def labels(self):
        return {PREFIX + "managed": "1", PREFIX + "scope": self.scope,
                PREFIX + "slot": str(self.index)}

    def __call__(self):
        self.lease.__exit__(None, None, None)

    def args(self):
        return [arg for key, value in self.labels.items() for arg in ("--label", f"{key}={value}")]

    @property
    def record_path(self):
        return self.root / "docker_slots" / f"{self.index}.json"

    def prepare(self, workdir):
        """Durable intent precedes docker run, including uncertain creation."""
        path = self.record_path
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".slot-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"scope": self.scope, "slot": self.index, "name": self.name,
                           "workspace": os.path.normcase(os.path.realpath(workdir))}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def forget(self):
        self.record_path.unlink(missing_ok=True)

    def reconcile(self, cfg):
        ids = _run(cfg, "ps", "-aq", "--filter", f"label={PREFIX}managed=1",
                   "--filter", f"label={PREFIX}scope={self.scope}",
                   "--filter", f"label={PREFIX}slot={self.index}").split()
        removed = []
        for container_id in ids:
            # Recheck labels and exact name immediately before removal. Never
            # infer ownership from a name prefix or process ID alone.
            info = _inspect(cfg, container_id)
            if info is None:
                continue
            labels = info.get("Config", {}).get("Labels") or {}
            if (any(labels.get(key) != value for key, value in self.labels.items())
                    or info.get("Name") != "/" + self.name):
                raise RuntimeError("Docker ownership mismatch; refusing orphan removal")
            _run(cfg, "rm", "-f", container_id)
            removed.append(container_id)
        self.forget()  # daemon confirmed no managed resource remains in this slot
        return removed


def acquire_slot(cfg):
    from research_agent.config import get_data_dir
    (get_data_dir() / "docker_slots").mkdir(parents=True, exist_ok=True)
    for index in range(cfg.max_containers):
        lease = _lease(index)
        try:
            lease.__enter__()
        except RuntimeError:
            continue
        return DockerSlot(index, scope_id(), lease)
    raise RuntimeError("Docker capacity reached; retry after an active container closes")


def reconcile_containers(cfg):
    """Scan owned slots, including slots beyond a newly reduced capacity limit.

    Busy slots belong to live hosts and are skipped. Failure to inspect/remove a
    free slot aborts admission; we do not silently release an uncertain resource.
    """
    scope = scope_id()
    ids = _run(cfg, "ps", "-aq", "--filter", f"label={PREFIX}managed=1",
               "--filter", f"label={PREFIX}scope={scope}").split()
    from research_agent.config import get_data_dir
    indices = {int(path.stem) for path in (get_data_dir() / "docker_slots").glob("*.json")
               if path.stem.isdecimal() and len(path.stem) <= 6 and str(int(path.stem)) == path.stem}
    for container_id in ids:
        info = _inspect(cfg, container_id)
        if info is None:
            continue
        labels = info.get("Config", {}).get("Labels") or {}
        raw = labels.get(PREFIX + "slot", "")
        if (labels.get(PREFIX + "managed") != "1" or labels.get(PREFIX + "scope") != scope
                or not raw.isdecimal() or str(int(raw)) != raw or len(raw) > 6):
            raise RuntimeError("Invalid Docker ownership labels; explicit reconciliation required")
        indices.add(int(raw))
    removed = []
    for index in sorted(indices):
        lease = _lease(index)
        try:
            lease.__enter__()
        except RuntimeError:
            continue
        try:
            removed.extend(DockerSlot(index, scope, lease).reconcile(cfg))
        finally:
            lease.__exit__(None, None, None)
    return removed


def reconcile_before_request():
    """Recover before any new request can touch a previously managed workspace."""
    from research_agent.config import get_data_dir
    if not any((get_data_dir() / "docker_slots").glob("*.json")):
        return []
    from research_agent.sandbox import get_sandbox_config, docker_available
    cfg = get_sandbox_config()
    if not docker_available(cfg.docker_bin):
        raise RuntimeError("Cannot verify previous Docker resources: start Docker before admitting requests")
    return reconcile_containers(cfg)
