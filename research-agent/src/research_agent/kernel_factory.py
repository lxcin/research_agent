"""Stable application kernels, shared by CLI, HTTP and chat entrypoints."""
from threading import Lock
from typing import Callable

from research_agent.kernel import AgentKernel, FunctionCallingKernel

KernelFactory = Callable[[], AgentKernel]
_lock = Lock()
_default_factory: KernelFactory = FunctionCallingKernel
_default_kernel: AgentKernel = FunctionCallingKernel()


def set_default_kernel_factory(factory: KernelFactory) -> KernelFactory:
    """Construct and install a stable kernel; return the previous factory.

    Configure at startup or explicit strategy replacement. Construction failure
    leaves the previous kernel installed. In-flight runs retain their instance.
    Kernels must keep request state local to run and support concurrent calls.
    """
    if not callable(factory):
        raise TypeError("kernel factory must be callable")
    replacement = factory()
    if not isinstance(replacement, AgentKernel):
        raise TypeError("kernel factory must return an AgentKernel")
    global _default_factory, _default_kernel
    with _lock:
        previous = _default_factory
        _default_factory = factory
        _default_kernel = replacement
    return previous


def get_kernel(kernel: AgentKernel | None = None) -> AgentKernel:
    """Resolve an explicit strategy or reuse the installed application kernel."""
    if kernel is None:
        with _lock:
            return _default_kernel
    if not isinstance(kernel, AgentKernel):
        raise TypeError("kernel must be an AgentKernel")
    return kernel


def create_kernel(kernel: AgentKernel | None = None) -> AgentKernel:
    """Compatibility name; resolves the installed instance without construction."""
    return get_kernel(kernel)
