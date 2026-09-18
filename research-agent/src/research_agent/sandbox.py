"""Sandboxed command execution for the shell tool.

Two backends:
  - local  : subprocess with shell=True, cwd=workspace. NO isolation — only the
             guardrail + HITL approval stand between the command and the host.
  - docker : one ephemeral container per command. Only the workspace is mounted
             (at /work), network is off by default, and CPU/mem/pid are capped.
             The container is --rm'd afterward, so nothing outside the bind
             mount survives.

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
from dataclasses import dataclass, field

DEFAULT_IMAGE = "python:3.11-slim"
MOUNT_TARGET = "/work"

_DOCKER_CACHE: dict[str, bool] = {}


@dataclass
class SandboxConfig:
    backend: str = "auto"            # auto | local | docker
    image: str = DEFAULT_IMAGE
    network: bool = False            # False = --network none inside the sandbox
    memory: str = "512m"
    cpus: str = "1.0"
    pids_limit: int = 256
    read_only: bool = True           # read-only rootfs; workspace bind stays writable
    extra_args: list[str] = field(default_factory=list)
    docker_bin: str = "docker"


def get_sandbox_config() -> SandboxConfig:
    from research_agent.config import load_config
    cfg = (load_config().get("shell") or {})
    return SandboxConfig(
        backend=str(cfg.get("backend", "auto")).lower(),
        image=str(cfg.get("image", DEFAULT_IMAGE)),
        network=bool(cfg.get("network", False)),
        memory=str(cfg.get("memory", "512m")),
        cpus=str(cfg.get("cpus", "1.0")),
        pids_limit=int(cfg.get("pids_limit", 256)),
        read_only=bool(cfg.get("read_only", True)),
        extra_args=list(cfg.get("extra_args", []) or []),
        docker_bin=str(cfg.get("docker_bin", "docker")),
    )


def docker_available(docker_bin: str = "docker") -> bool:
    """True if a working docker CLI/daemon is present. Cached per binary."""
    if docker_bin in _DOCKER_CACHE:
        return _DOCKER_CACHE[docker_bin]
    ok = False
    if shutil.which(docker_bin):
        try:
            r = subprocess.run([docker_bin, "info"], capture_output=True,
                               text=True, timeout=10)
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


def build_docker_argv(command: str, workdir: str, cfg: SandboxConfig) -> list[str]:
    """Build the `docker run` argv for one command inside the sandbox."""
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
    argv += list(cfg.extra_args)
    argv += [cfg.image, "sh", "-lc", command]
    return argv


def run_command(command: str, workdir: str, timeout: int,
                cfg: SandboxConfig | None = None) -> dict:
    """Run `command` via the resolved backend.

    Returns a dict: success, backend, returncode, stdout, stderr, warning/error.
    Never raises.
    """
    cfg = cfg or get_sandbox_config()
    backend, warning = resolve_backend(cfg)

    if backend == "docker" and warning:
        return {"success": False, "backend": "docker", "returncode": -1,
                "stdout": "", "stderr": warning, "error": warning}

    try:
        if backend == "docker":
            r = subprocess.run(build_docker_argv(command, workdir, cfg),
                               capture_output=True, text=True, timeout=timeout)
        else:
            r = subprocess.run(command, shell=True, capture_output=True,
                               text=True, timeout=timeout, cwd=workdir)
        return {"success": r.returncode == 0, "backend": backend,
                "returncode": r.returncode, "stdout": r.stdout,
                "stderr": r.stderr, "warning": warning}
    except subprocess.TimeoutExpired:
        return {"success": False, "backend": backend, "returncode": -1,
                "stdout": "", "stderr": "", "error": "timeout", "warning": warning}
    except Exception as e:  # docker missing, mount error, ...
        return {"success": False, "backend": backend, "returncode": -1,
                "stdout": "", "stderr": str(e), "error": str(e), "warning": warning}
