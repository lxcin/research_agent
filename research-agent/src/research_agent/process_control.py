"""Cooperative cancellation of local command trees without reader threads."""
import os
import signal
import subprocess
import tempfile
import time
from research_agent.execution import Cancelled


def stop_container(binary, name, workdir):
    result = subprocess.run([binary, "rm", "-f", name], capture_output=True,
                            text=True, errors="replace", timeout=15)
    if result.returncode:
        error = result.stderr or ""
        if "no such container" in error.lower() and name in error:
            return
        from research_agent.execution import quarantine
        quarantine(workdir, f"Container stop unconfirmed: {name}")
        raise RuntimeError(result.stderr or "docker rm failed")


def run_docker(command, workdir, timeout, cancellation, *, session=None, cfg=None):
    from research_agent.sandbox import get_sandbox_config, build_docker_argv
    cfg = session.cfg if session is not None else cfg or get_sandbox_config()
    if session is not None:
        session.start()
        name = session._container
        argv = [cfg.docker_bin, "exec", "-w", "/work", name, "sh", "-lc", command]
    from research_agent.sandbox import own_oneshot
    owner = None
    if session is None:
        owner = own_oneshot(workdir, cfg, "")
        name = owner._container
        argv = build_docker_argv(command, workdir, cfg)
        argv[2:2] = ["--name", name, *owner._slot_release.args()]
    def abort():
        stop_container(cfg.docker_bin, name, workdir)
        if session is not None:
            session._container = None
            session.close()
        if owner is not None:
            owner._container = None
    try:
        result = run_local(argv, workdir, timeout, cancellation, on_abort=abort)
    finally:
        if owner is not None:
            try:
                owner.close()
            except Exception as exc:
                from research_agent.execution import RecoveryRequired, quarantine
                quarantine(workdir, "One-shot container cleanup unconfirmed")
                raise RecoveryRequired(f"One-shot container cleanup incomplete: {exc}") from exc
    result["backend"] = "docker"
    return result


def run_local(command, cwd, timeout, cancellation, on_abort=None):
    cancellation.check()
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        proc = subprocess.Popen(command, cwd=cwd, shell=isinstance(command, str), stdout=stdout, stderr=stderr,
                                start_new_session=os.name != "nt",
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        deadline = time.monotonic() + timeout
        try:
            while proc.poll() is None:
                cancellation.check()
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(command, timeout)
                time.sleep(0.05)
        except BaseException:
            cleanup_error = None
            if on_abort is not None:
                try:
                    on_abort()
                except BaseException as exc:
                    cleanup_error = exc
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc.wait(timeout=10)
            if cleanup_error is not None:
                from research_agent.execution import RecoveryRequired
                raise RecoveryRequired(f"Container stop not confirmed: {cleanup_error}")
            raise
        stdout.seek(0)
        stderr.seek(0)
        return {"success": proc.returncode == 0, "returncode": proc.returncode,
                "stdout": stdout.read(1024 * 1024).decode("utf-8", errors="replace"),
                "stderr": stderr.read(1024 * 1024).decode("utf-8", errors="replace"), "backend": "local"}
