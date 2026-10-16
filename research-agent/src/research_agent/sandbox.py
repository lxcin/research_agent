"""Sandboxed command execution — session-based (OpenAI-SandboxAgent-inspired).

The backend selects isolation: local | docker (auto resolves).
A sandbox lives for one agent run: created lazily on the first shell_exec,
reused for subsequent commands, then closed at run end. No cross-run containers.
Background commands own a separate one-shot container until completion/cancellation.

Why session-scoped, not per-command: `docker run` + a git snapshot per command
pays the container-start cost N times for a batch of N scripts. A session is a
live environment (a long-lived container) reused via `docker exec`, so a batch of
commands shares one start. The workspace is bind-mounted, so files persist on the
host regardless; see `research_agent.checkpoint` for rollback.

Backends:
  - local  : subprocess with shell=True, cwd=workspace. NO isolation — only the
             guardrail + HITL approval stand between the command and the host.
  - docker : one long-lived container per session. Only the workspace is mounted
             (at /work), network is off by default, and CPU/mem/pid are capped.
             Commands run via `docker exec`; the container is removed on close.

Backend selection (config `shell.backend`):
  - "local"  : always local
  - "docker" : always docker; if docker is unavailable the command FAILS rather
               than silently degrading to the unsandboxed path
  - "auto"   : docker when available, else local (with a warning)

Design note: binding the workspace means the container *cannot* protect the
workspace itself — writes land on the host directly. Host isolation and workspace
rollback are separate concerns; see `research_agent.checkpoint` for the latter.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

DEFAULT_IMAGE = "python:3.11-slim"
MOUNT_TARGET = "/work"

_DOCKER_CACHE: dict[str, bool] = {}


@dataclass(frozen=True)
class SharedMount:
    source: str
    target: str

    def __post_init__(self):
        target = PurePosixPath(self.target)
        if not target.is_absolute() or not self.target.startswith("/shared/") or ".." in target.parts:
            raise ValueError("Shared mounts must target a child of /shared")
        if any(char in self.source + self.target for char in (",", "\n", "\r")) or not Path(self.source).is_absolute() or not Path(self.source).exists():
            raise ValueError("Shared mount source must exist and mount paths cannot contain commas/newlines")


@dataclass
class SandboxConfig:
    backend: str = "auto"            # auto | local | docker
    session: str = "ephemeral"       # only run-scoped sessions are supported
    image: str = DEFAULT_IMAGE
    network: bool = False            # False = --network none inside the sandbox
    memory: str = "512m"
    cpus: str = "1.0"
    pids_limit: int = 256
    read_only: bool = True           # read-only rootfs; workspace bind stays writable
    extra_args: list[str] = field(default_factory=list)
    docker_bin: str = "docker"
    max_containers: int = 2
    shared_mounts: list[SharedMount] = field(default_factory=list)

    def __post_init__(self):
        if self.session != "ephemeral":
            raise ValueError("Only ephemeral sandbox sessions are supported")
        if isinstance(self.max_containers, bool) or not isinstance(self.max_containers, int) or self.max_containers < 1:
            raise ValueError("max_containers must be positive")
        targets = [PurePosixPath(m.target) for m in self.shared_mounts]
        if any(a == b or a in b.parents or b in a.parents for i, a in enumerate(targets) for b in targets[i+1:]):
            raise ValueError("Shared mount targets cannot overlap")


def get_sandbox_config() -> SandboxConfig:
    from research_agent.config import load_config
    cfg = (load_config().get("shell") or {})
    session = str(cfg.get("session", "ephemeral")).lower()
    if session == "persistent":
        import logging
        logging.getLogger(__name__).warning(
            "shell.session=persistent is retired; using per-run ephemeral containers")
        session = "ephemeral"
    return SandboxConfig(
        backend=str(cfg.get("backend", "auto")).lower(),
        session=session,
        image=str(cfg.get("image", DEFAULT_IMAGE)),
        network=bool(cfg.get("network", False)),
        memory=str(cfg.get("memory", "512m")),
        cpus=str(cfg.get("cpus", "1.0")),
        pids_limit=int(cfg.get("pids_limit", 256)),
        read_only=bool(cfg.get("read_only", True)),
        extra_args=list(cfg.get("extra_args", []) or []),
        docker_bin=str(cfg.get("docker_bin", "docker")),
        max_containers=cfg.get("max_containers", 2),
        shared_mounts=[SharedMount(**item) for item in cfg.get("shared_mounts", [])],
    )


def docker_available(docker_bin: str = "docker") -> bool:
    """True if a working docker CLI/daemon is present. Cached per binary."""
    if docker_bin in _DOCKER_CACHE:
        return _DOCKER_CACHE[docker_bin]
    ok = False
    if shutil.which(docker_bin):
        try:
            r = subprocess.run([docker_bin, "info"], capture_output=True,
                               text=True, errors="replace", timeout=10)
            ok = r.returncode == 0
        except Exception:
            ok = False
    _DOCKER_CACHE[docker_bin] = ok
    return ok


def reset_docker_cache():
    _DOCKER_CACHE.clear()


def resolve_backend(cfg: SandboxConfig) -> tuple[str, str | None]:
    """Return (backend, warning). warning is set when the choice is degraded."""
    if cfg.backend == "local":
        return "local", None
    if cfg.backend == "docker":
        if docker_available(cfg.docker_bin):
            return "docker", None
        return "docker", "shell.backend=docker but docker is unavailable"
    # auto
    if docker_available(cfg.docker_bin):
        return "docker", None
    return "local", "docker unavailable — running UNSANDBOXED (local)"


def _mount_spec(workdir: str) -> str:
    p = os.path.abspath(workdir)
    if os.name == "nt":
        p = p.replace("\\", "/")
    return f"{p}:{MOUNT_TARGET}"


def _shared_mount_args(cfg):
    args = []
    for mount in cfg.shared_mounts:
        source = Path(mount.source).resolve().as_posix()
        args += ["--mount", f"type=bind,source={source},target={mount.target},readonly"]
    return args


def acquire_docker_slot(cfg):
    """Nonblocking cross-process admission; all hosts must share data_dir/config."""
    from research_agent.docker_ownership import acquire_slot
    return acquire_slot(cfg)


def build_docker_argv(command: str, workdir: str, cfg: SandboxConfig) -> list[str]:
    """Build the one-shot `docker run` argv for a single command (legacy path)."""
    argv: list[str] = [
        cfg.docker_bin, "run", "--rm", "--init",
        "--network", "bridge" if cfg.network else "none",
        "-v", _mount_spec(workdir), "-w", MOUNT_TARGET,
        "--memory", cfg.memory,
        "--cpus", cfg.cpus,
        "--pids-limit", str(cfg.pids_limit),
        "--security-opt", "no-new-privileges",
    ]
    if cfg.read_only:
        argv += ["--read-only", "--tmpfs", "/tmp", "-e", "HOME=/tmp"]
    argv += _shared_mount_args(cfg)
    argv += list(cfg.extra_args)
    argv += [cfg.image, "sh", "-lc", command]
    return argv


def build_session_argv(workdir: str, cfg: SandboxConfig, name: str) -> list[str]:
    """Build the `docker run -d` argv for a long-lived session container."""
    argv: list[str] = [
        cfg.docker_bin, "run", "-d", "--rm", "--init", "--name", name,
        "--network", "bridge" if cfg.network else "none",
        "-v", _mount_spec(workdir), "-w", MOUNT_TARGET,
        "--memory", cfg.memory,
        "--cpus", cfg.cpus,
        "--pids-limit", str(cfg.pids_limit),
        "--security-opt", "no-new-privileges",
    ]
    if cfg.read_only:
        argv += ["--read-only", "--tmpfs", "/tmp", "-e", "HOME=/tmp"]
    argv += _shared_mount_args(cfg)
    argv += list(cfg.extra_args)
    argv += [cfg.image, "sleep", "infinity"]
    return argv


# ── Session: a live execution environment reused across commands ──

class SandboxSession:
    """A live sandbox reused across multiple commands within its lifetime.

    backend=local  -> a lightweight handle (no isolation); exec runs in workdir.
    backend=docker -> a long-lived container started once and reused via exec.
    """

    def __init__(self, workdir: str, cfg: SandboxConfig, backend: str,
                 warning: str | None = None,
                 session_id: str | None = None):
        self.workdir = workdir
        self.cfg = cfg
        self.backend = backend
        self.warning = warning
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.last_used = time.time()
        self._started = False
        self._closed = False
        self._container: str | None = None
        self._slot_release = None

    @property
    def container_name(self) -> str:
        if self._slot_release is not None:
            return self._slot_release.name
        return f"pp-sbx-{self.session_id}"

    # ── lifecycle ──

    def start(self) -> "SandboxSession":
        if self._closed:
            raise RuntimeError("sandbox session is closed")
        if self._started:
            return self
        if self.backend == "docker":
            if self._slot_release is not None:
                raise RuntimeError("Previous container startup requires cleanup before retry")
            self._slot_release = acquire_docker_slot(self.cfg)
            try:
                self._start_docker()
            except BaseException:
                if self._container is not None:
                    with _CLEANUP_LOCK:
                        _FAILED_CLEANUP[id(self)] = self
                else:
                    self._slot_release()
                    self._slot_release = None
                raise
        self._started = True
        return self

    def _start_docker(self):
        if not docker_available(self.cfg.docker_bin):
            raise RuntimeError("docker is unavailable; cannot start docker sandbox session")
        self._slot_release.reconcile(self.cfg)
        name = self.container_name
        argv = build_session_argv(self.workdir, self.cfg, name)
        argv[2:2] = self._slot_release.args()
        self._slot_release.prepare(self.workdir)
        self._container = name
        r = subprocess.run(argv, capture_output=True, text=True,
                           errors="replace", timeout=120)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "docker run failed").strip())
        self._container = name
        # Best-effort teardown if the interpreter exits before close().
        try:
            import atexit
            atexit.register(self.close)
        except Exception:
            pass

    def exec(self, command: str, timeout: int) -> dict:
        """Run one command in this session. Never raises (mirrors run_command)."""
        if self._closed:
            return {"success": False, "backend": self.backend, "returncode": -1,
                    "stdout": "", "stderr": "session closed", "error": "session closed"}
        try:
            self.start()
        except Exception as e:  # docker missing, run failed, ...
            return {"success": False, "backend": self.backend, "returncode": -1,
                    "stdout": "", "stderr": str(e), "error": str(e),
                    "warning": self.warning}
        self.last_used = time.time()
        return self._exec(command, timeout)

    def _exec(self, command: str, timeout: int) -> dict:
        try:
            if self.backend == "docker":
                r = subprocess.run(
                    [self.cfg.docker_bin, "exec", "-w", MOUNT_TARGET,
                     self._container, "sh", "-lc", command],
                    capture_output=True, encoding="utf-8",
                    errors="replace", timeout=timeout)
            else:
                # text=True uses the locale encoding (cp936/UTF-8); errors="replace"
                # avoids UnicodeDecodeError on mixed-encoding child output (Windows).
                r = subprocess.run(command, shell=True, capture_output=True,
                                   text=True, errors="replace", timeout=timeout,
                                   cwd=self.workdir)
            return {"success": r.returncode == 0, "backend": self.backend,
                    "returncode": r.returncode, "stdout": r.stdout,
                    "stderr": r.stderr, "warning": self.warning}
        except subprocess.TimeoutExpired:
            self._close_after_uncertain_exec()
            return {"success": False, "backend": self.backend, "returncode": -1,
                    "stdout": "", "stderr": "", "error": "timeout",
                    "warning": self.warning}
        except Exception as e:  # docker missing, exec error, ...
            self._close_after_uncertain_exec()
            return {"success": False, "backend": self.backend, "returncode": -1,
                    "stdout": "", "stderr": str(e), "error": str(e),
                    "warning": self.warning}

    def _close_after_uncertain_exec(self):
        if self.backend != "docker":
            return
        try:
            self.close()
        except Exception as exc:
            from research_agent.execution import RecoveryRequired, quarantine
            quarantine(self.workdir, "Docker exec cleanup unconfirmed")
            raise RecoveryRequired(f"Docker exec cleanup incomplete: {exc}") from exc

    def stop(self) -> dict:
        """Persist workspace state.

        The workspace is bind-mounted, so there is nothing to copy out; this is a
        hook matching the session lifecycle (stop != close). Workspace rollback is
        handled by research_agent.checkpoint.
        """
        return {"success": True, "persisted": bool(self.workdir)}

    def close(self):
        if self.backend == "docker" and self._container:
            try:
                result = subprocess.run([self.cfg.docker_bin, "rm", "-f", self._container],
                               capture_output=True, text=True, errors="replace",
                               timeout=30)
                absent = "no such container" in getattr(result, "stderr", "").lower() and self._container in getattr(result, "stderr", "")
                if result.returncode != 0 and not absent:
                    raise RuntimeError(f"Container removal failed ({result.returncode}): {self._container}")
            except Exception:
                # Retain a retryable owner even if the child/request state goes away.
                with _CLEANUP_LOCK:
                    _FAILED_CLEANUP[id(self)] = self
                raise
        self._container = None
        if self._slot_release is not None:
            try:
                self._slot_release.forget()
                self._slot_release()
            except Exception:
                with _CLEANUP_LOCK:
                    _FAILED_CLEANUP[id(self)] = self
                raise
            self._slot_release = None
        self._closed = True
        with _CLEANUP_LOCK:
            _FAILED_CLEANUP.pop(id(self), None)

    def is_alive(self) -> bool:
        if self._closed:
            return False
        if not self._started:
            return self._container is None  # failed/uncertain startup must be cleaned before reuse
        if self.backend == "docker":
            if not self._container:
                return False
            try:
                r = subprocess.run(
                    [self.cfg.docker_bin, "inspect", "-f", "{{.State.Running}}",
                     self._container],
                    capture_output=True, text=True, errors="replace", timeout=15)
                return r.stdout.strip().lower() == "true"
            except Exception:
                return False
        return True

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()
        return False


def create_session(workdir: str, cfg: SandboxConfig | None = None) -> SandboxSession:
    """Create a (not-yet-started) session for `workdir` from the resolved backend."""
    cfg = cfg or get_sandbox_config()
    backend, warning = resolve_backend(cfg)
    return SandboxSession(workdir, cfg, backend, warning=warning)


# ── Session registry / lifecycle resolution ──
#
# Each state owns its run-scoped session. Failed cleanup remains registered
# until removal is confirmed, retaining its capacity permit.
_FAILED_CLEANUP: dict[int, SandboxSession] = {}
_CLEANUP_LOCK = threading.RLock()


def _close_ephemeral(session):
    if session is not None:
        session.close()


from research_agent.state_components import StateComponent, ComponentStore
SESSION_FIELD = StateComponent("shell.session", lambda: None, owner="shell", lifetime="request",
    value_type=(SandboxSession, type(None)), cleanup=_close_ephemeral, fork=lambda session: None)


def _session_store(state):
    # Lightweight host objects may also use the public sandbox API.
    store = getattr(state, "components", None)
    if store is None:
        store = ComponentStore()
        state.components = store
    store.inject(SESSION_FIELD)
    caps = getattr(state, "caps", {})
    legacy = caps.get("sandbox")
    if legacy is not None:
        if store.get(SESSION_FIELD.key) is None:
            store.set(SESSION_FIELD.key, legacy)
        caps.pop("sandbox", None)
    return store


def get_session(state, workdir: str, cfg: SandboxConfig | None = None) -> SandboxSession:
    """Attach a lazy per-run session, reused by commands until end_run()."""
    cfg = cfg or get_sandbox_config()
    store = _session_store(state)

    sess = store.get(SESSION_FIELD.key)
    if sess is not None and sess.is_alive():
        return sess
    if sess is not None:
        sess.close()
    sess = create_session(workdir, cfg)
    store.set(SESSION_FIELD.key, sess)
    return sess


def end_run(state):
    """Close the run session, retaining ownership if cleanup fails."""
    store = getattr(state, "components", None)
    if store is None or SESSION_FIELD.key not in store.keys():
        return
    sess = store.get(SESSION_FIELD.key)
    if sess is not None:
        sess.close()
    store.set(SESSION_FIELD.key, None)


def close_all_sessions():
    """Retry failed cleanup; active run sessions remain owned by their runs."""
    with _CLEANUP_LOCK:
        errors = []
        sessions = dict(_FAILED_CLEANUP)
        for sess in sessions.values():
            try:
                sess.close()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("Sandbox cleanup incomplete", errors)


def close_workspace_sessions(workspace):
    """Called only after the host owns the workspace lease (no active jobs)."""
    key = os.path.normcase(os.path.realpath(workspace))
    with _CLEANUP_LOCK:
        sessions = dict(_FAILED_CLEANUP)
        for session in sessions.values():
            if os.path.normcase(os.path.realpath(session.workdir)) == key:
                session.close()


def own_oneshot(workdir, cfg, name):
    owner = SandboxSession(workdir, cfg, "docker")
    owner._slot_release = acquire_docker_slot(cfg)
    try:
        owner._slot_release.reconcile(cfg)
        owner._slot_release.prepare(workdir)
    except BaseException:
        owner._slot_release()
        owner._slot_release = None
        raise
    owner._container = owner.container_name
    return owner


# ── One-shot execution (legacy / long background tasks) ──

def run_command(command: str, workdir: str, timeout: int,
                cfg: SandboxConfig | None = None) -> dict:
    """Run one command in a throwaway sandbox (independent lifetime).

    Used for background tasks (which may outlive the run's session) and as the
    stable one-shot primitive; interactive foreground commands go through a
    reused SandboxSession instead.
    """
    cfg = cfg or get_sandbox_config()
    backend, warning = resolve_backend(cfg)

    if backend == "docker" and warning:
        return {"success": False, "backend": "docker", "returncode": -1,
                "stdout": "", "stderr": warning, "error": warning}

    owner = None
    try:
        if backend == "docker":
            name = "pp-once-" + uuid.uuid4().hex
            owner = own_oneshot(workdir, cfg, name)
            name = owner._container
            argv = build_docker_argv(command, workdir, cfg)
            argv[2:2] = ["--name", name, *owner._slot_release.args()]
            r = subprocess.run(argv,
                               capture_output=True, encoding="utf-8",
                               errors="replace", timeout=timeout)
        else:
            # text=True uses the locale encoding (cp936/UTF-8); errors="replace"
            # avoids UnicodeDecodeError on mixed-encoding child output (Windows).
            r = subprocess.run(command, shell=True, capture_output=True,
                               text=True, errors="replace", timeout=timeout,
                               cwd=workdir)
        return {"success": r.returncode == 0, "backend": backend,
                "returncode": r.returncode, "stdout": r.stdout,
                "stderr": r.stderr, "warning": warning}
    except subprocess.TimeoutExpired:
        return {"success": False, "backend": backend, "returncode": -1,
                "stdout": "", "stderr": "", "error": "timeout", "warning": warning}
    except Exception as e:  # docker missing, mount error, ...
        return {"success": False, "backend": backend, "returncode": -1,
                "stdout": "", "stderr": str(e), "error": str(e), "warning": warning}
    finally:
        if owner is not None:
            try:
                owner.close()
            except Exception as exc:
                from research_agent.execution import RecoveryRequired, quarantine
                quarantine(workdir, "One-shot container cleanup unconfirmed")
                raise RecoveryRequired(f"One-shot container cleanup incomplete: {exc}") from exc


try:
    import atexit as _atexit
    _atexit.register(close_all_sessions)
except Exception:
    pass
