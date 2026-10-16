import pytest
from research_agent.execution import checkpoint, execute_once, RecoveryRequired
from research_agent.kernel import FunctionCallingKernel, KernelRequest, ModelResponse, ToolCall, ToolObservation
from research_agent.tools.schema import ToolResult
from research_agent.state_components import ComponentStore, StateComponent


def test_resume_after_tool_result_saved_before_loop_checkpoint():
    effects = []
    class Crash(BaseException):
        pass
    class Host:
        crash = True
        def checkpoint(self, payload=None):
            return checkpoint("resume", "same-request", payload)
        def prepare(self):
            return [{"role": "user", "content": "write"}]
        def select(self, messages):
            return ModelResponse() if any(m["role"] == "tool" for m in messages) else ModelResponse(
                tool_calls=(ToolCall("c1", "write", {}),))
        def execute(self, call):
            def invoke():
                effects.append(1)
                return ToolResult.ok(value=42)
            result = execute_once("resume", call.id, call.name, call.params, invoke)
            return ToolObservation(result.success, result.data)
        def after_tool(self, call, messages):
            if self.crash:
                raise Crash()
        def answer(self, messages, *, fallback=False):
            return "done"
        def set_round(self, number):
            pass
        def emit(self, *args):
            pass
    kernel, host = FunctionCallingKernel(), Host()
    with pytest.raises(Crash):
        kernel.run(host, KernelRequest("write"))
    host.crash = False
    assert kernel.run(host, KernelRequest("write")).response == "done"
    assert effects == [1]
    with pytest.raises(RecoveryRequired):
        checkpoint("resume", "different-request")


def test_stale_state_writer_cannot_overwrite_newer_snapshot(tmp_path):
    spec = StateComponent("value", int, encode=int, decode=int)
    first, second = ComponentStore(), ComponentStore()
    first.inject(spec)
    second.inject(spec)
    path = tmp_path / "state.json"
    first.save(path)
    second.restore(path)
    first.set("value", 1)
    first.save(path)
    second.set("value", 2)
    with pytest.raises(RuntimeError, match="conflict"):
        second.save(path)
    second.restore(path)
    assert second.get("value") == 1
