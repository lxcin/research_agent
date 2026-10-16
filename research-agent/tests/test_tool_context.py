"""Current source is readable; completed edit payloads are archived, not replayed."""
import hashlib
import json
from pathlib import Path

import pytest

from research_agent.context_budget import artifact_directory, externalize
from research_agent.execution import checkpoint_payload
from research_agent.models import AgentState
from research_agent.runtime import FunctionCallingRuntime, RuntimeContext
from research_agent.kernel import FunctionCallingKernel, ToolCall
from research_agent.tools.builtin.context_results import read_result
from research_agent.tools.builtin.filesystem import _handle_file_read, _handle_file_edit, _handle_file_write


@pytest.fixture
def state(tmp_path):
    return AgentState(workspace_dir=str(tmp_path), active_chat_id="tool-context")


def test_read_then_edit_keeps_current_excerpt_and_archives_completed_source(state, monkeypatch):
    from research_agent.tools import ToolRegistry, get_registry
    from research_agent.tools.builtin import register_builtins
    monkeypatch.setattr(ToolRegistry, "_instance", None)
    register_builtins()
    registry = get_registry()
    registry.bind_state(state)
    state._execution_id, state._kernel_id = "tool-context", "function_calling"
    path = Path(state.workspace_dir) / "code.txt"
    path.write_text("header\nOLD_SECRET\nfooter\n", encoding="utf-8")
    phases, final_messages = [], []
    def select(llm, messages, *args):
        phases.append(1)
        if len(phases) == 1:
            return {"tool_calls": [{"id": "read", "name": "file_read", "params": {"path": "code.txt"}}]}
        if len(phases) == 2:
            assert "OLD_SECRET" in json.dumps(messages)
            return {"tool_calls": [{"id": "edit", "name": "file_edit", "params": {
                "path": "code.txt", "old_string": "OLD_SECRET", "new_string": "NEW_SECRET",
                "expected_version": hashlib.sha256(path.read_bytes()).hexdigest()}}]}
        final_messages.extend(messages)
        results = {m["tool_call_id"]: json.loads(m["content"]) for m in messages if m["role"] == "tool"}
        assert "OLD_SECRET" in results["read"]["content"]
        assert not results["read"].get("_history_summary")
        assert "-OLD_SECRET" in results["edit"]["diff"] and "+NEW_SECRET" in results["edit"]["diff"]
        assert results["read"]["path"] == "code.txt"
        assert results["edit"]["completed"] and results["edit"]["verification"] == "read_back_match"
        assert results["edit"]["changes"] == [{"kind": "replace", "old_start_line": 2,
            "old_line_count": 1, "new_start_line": 2, "new_line_count": 1}]
        return {}
    ctx = RuntimeContext(None, "edit", state, registry=registry, workspace_dir=state.workspace_dir,
        chat_id=state.active_chat_id, emit=lambda *a: None,
        call_llm_with_tools=select, stream_response=lambda *a: "done")
    FunctionCallingRuntime(kernel=FunctionCallingKernel()).run(ctx)
    assert state.final_response == "done" and path.read_text() == "header\nNEW_SECRET\nfooter\n"
    saved = checkpoint_payload(state._execution_id)
    from research_agent.tool_context import compact_completed
    compact_completed(final_messages, registry, state, fold_deferred=True)
    compact_completed(saved["messages"], registry, state, fold_deferred=True)
    saved_read = next(m for m in saved["messages"] if m.get("tool_call_id") == "read")
    assert "content" not in json.loads(saved_read["content"])
    edit_call = next(call for m in final_messages for call in m.get("tool_calls", []) if call["id"] == "edit")
    reference = json.loads(edit_call["function"]["arguments"])
    assert reference["summary"] == {"path": "code.txt"}
    archived = read_result({"artifact_id": reference["_stored_call"]}, None, state, None)
    assert archived.success and json.loads(archived.data["text"])["params"]["new_string"] == "NEW_SECRET"
    read_message = next(m for m in final_messages if m.get("tool_call_id") == "read")
    raw = read_result({"artifact_id": json.loads(read_message["content"])["_tool_result_id"]}, None, state, None)
    assert "OLD_SECRET" in raw.data["text"]


def test_even_small_outputs_are_stored_and_remain_conversation_scoped(state):
    data = {"completed": True, "path": "file.txt"}
    visible = externalize(data, state, always_store=True)
    assert visible["completed"]
    archived = read_result({"artifact_id": visible["_tool_result_id"]}, None, state, None)
    assert json.loads(archived.data["text"]) == data
    other = AgentState(workspace_dir=state.workspace_dir, active_chat_id="different")
    assert not read_result({"artifact_id": visible["_tool_result_id"]}, None, other, None).success


def test_file_read_pagination_does_not_silently_discard_source(state):
    text = "first\n" + "x" * 9000 + "\nlast\n\n"
    (Path(state.workspace_dir) / "long.txt").write_bytes(text.encode())
    pieces, cursor = [], {"line": 1, "column": 1}
    while True:
        result = _handle_file_read({"path": "long.txt", "start_line": cursor["line"], "start_column": cursor["column"], "limit": 137}, None, state, lambda *a: None)
        assert result.success
        pieces.append(result.data["content"])
        assert result.data["sha256"] == hashlib.sha256(text.encode()).hexdigest()
        if not result.data["has_more"]:
            break
        assert result.data["next_cursor"] != cursor
        cursor = result.data["next_cursor"]
    assert "".join(pieces) == text


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_edit_reports_exact_range_and_preserves_untouched_bytes(state, newline):
    path = Path(state.workspace_dir) / "code.py"
    before = b"\xef\xbb\xbf" + newline.join([b"first", b"old", b"last", b""])
    path.write_bytes(before)
    version = _handle_file_read({"path": "code.py"}, None, state, None).data["version"]
    result = _handle_file_edit({"path": "code.py", "old_string": "old\n", "new_string": "new\n", "expected_version": version}, None, state, lambda *a: None)
    assert result.success and result.data["completed"]
    assert path.read_bytes() == before.replace(b"old", b"new")
    assert result.data["changes"][0]["old_start_line"] == 2
    assert result.data["changes"][0]["old_line_count"] == 1
    assert result.data["validation"] == "not_run_by_edit_tool"


def test_write_distinguishes_creation_overwrite_and_no_change(state):
    events = []
    def write(text):
        path = Path(state.workspace_dir) / "code.txt"
        version = _handle_file_read({"path": "code.txt"}, None, state, None).data["version"] if path.exists() else "absent"
        return _handle_file_write({"expected_version": version, "path": "code.txt", "content": text}, None, state,
                                  lambda kind, data: events.append(data))
    created = write("one\ntwo\n")
    assert created.data["operation"] == "create" and created.data["completed"]
    replaced = write("one\nthree\n")
    assert replaced.data["operation"] == "replace" and replaced.data["changes"][0]["new_start_line"] == 2
    unchanged = write("one\nthree\n")
    assert unchanged.data["completed"] and not unchanged.data["changed"] and not unchanged.data["changes"]


def test_read_back_mismatch_never_reports_edit_completed(state, monkeypatch):
    path = Path(state.workspace_dir) / "code.txt"
    path.write_bytes(b"old")
    version = _handle_file_read({"path": "code.txt"}, None, state, None).data["version"]
    import os
    original = os.replace
    def competing_write(source, target):
        original(source, target)
        if Path(target) == path:
            path.write_bytes(b"external change")
    monkeypatch.setattr(os, "replace", competing_write)
    events = []
    result = _handle_file_write({"expected_version": version, "path": "code.txt", "content": "desired"}, None, state,
                                lambda *a: events.append(a))
    assert not result.success and not result.data["completed"]
    assert result.data["verification"] == "read_back_mismatch" and events == []


def test_failed_match_is_not_an_edit_and_can_be_replanned(state):
    path = Path(state.workspace_dir) / "code.txt"
    path.write_bytes(b"human current implementation")
    version = _handle_file_read({"path": "code.txt"}, None, state, None).data["version"]
    result = _handle_file_edit({"path": "code.txt", "old_string": "old implementation", "new_string": "new", "expected_version": version}, None, state, lambda *a: None)
    assert not result.success and not result.data["completed"]
    assert result.data["verification"] == "not_attempted"
    assert path.read_bytes() == b"human current implementation"


def test_large_edit_keeps_completion_metadata_and_full_range_archive(state, monkeypatch):
    from research_agent.tools import ToolRegistry, get_registry
    from research_agent.tools.builtin import register_builtins
    from research_agent.runtime import RuntimeHost
    monkeypatch.setattr(ToolRegistry, "_instance", None)
    monkeypatch.setattr("research_agent.config.get_context_config", lambda: {"tool_result_tokens": 128})
    register_builtins()
    before = "".join(f"keep-{i}\nold-{i}\n" for i in range(60))
    path = Path(state.workspace_dir) / "code.txt"
    path.write_bytes(before.encode())
    version = _handle_file_read({"path": "code.txt"}, None, state, None).data["version"]
    host = RuntimeHost(RuntimeContext(None, "edit", state, registry=get_registry(), emit=lambda *a: None))
    result = host.execute(ToolCall("write", "file_write", {"expected_version": version, "path": "code.txt", "content": before.replace("old-", "new-")}))
    assert result.success and result.data["completed"]
    assert result.data["verification"] == "read_back_match" and result.data["path"] == "code.txt"
    key = result.data["_tool_result_id"]
    if result.data.get("_truncated"):
        assert result.data["artifact_id"] == key
    archived = json.loads((artifact_directory(state) / (key + ".json")).read_text(encoding="utf-8"))
    assert len(archived["changes"]) == 60


def test_plugin_declares_context_projection_without_kernel_file_rules(state, monkeypatch):
    from research_agent.tools import ToolRegistry, get_registry
    from research_agent.tools.builtin import register_builtins
    from research_agent.tools.schema import ToolSchema, ToolResult
    from research_agent.runtime import RuntimeHost
    monkeypatch.setattr(ToolRegistry, "_instance", None)
    register_builtins()
    registry = get_registry()
    registry.register(ToolSchema("custom", "opaque capability", {"type": "object"},
        lambda *a: ToolResult.ok(payload="PRIVATE_OUTPUT", count=1),
        context_call=lambda params: {"operation": "opaque"}, context_result=lambda data: {"count": data["count"]}))
    host = RuntimeHost(RuntimeContext(None, "task", state, registry=registry, emit=lambda *a: None))
    result = host.execute(ToolCall("custom", "custom", {"value": "PRIVATE_INPUT"}))
    assert "PRIVATE_OUTPUT" not in json.dumps(result.data)
    messages = [{"role": "assistant", "tool_calls": [{"id": "custom", "type": "function",
        "function": {"name": "custom", "arguments": json.dumps({"value": "PRIVATE_INPUT"})}}]},
        {"role": "tool", "tool_call_id": "custom", "content": json.dumps(result.data)}]
    host.checkpoint({"messages": messages})
    assert "PRIVATE_INPUT" not in json.dumps(messages)
    assert json.loads(messages[0]["tool_calls"][0]["function"]["arguments"])["summary"] == {"operation": "opaque"}


def test_pending_arguments_remain_executable_until_result_arrives(state, monkeypatch):
    from research_agent.tools import ToolRegistry, get_registry
    from research_agent.tools.builtin import register_builtins
    from research_agent.tool_context import compact_completed
    monkeypatch.setattr(ToolRegistry, "_instance", None)
    register_builtins()
    arguments = json.dumps({"expected_version": "absent", "path": "code.txt", "content": "pending source"})
    messages = [{"role": "assistant", "tool_calls": [{"id": "pending", "type": "function",
        "function": {"name": "file_write", "arguments": arguments}}]}]
    compact_completed(messages, get_registry(), state)
    assert messages[0]["tool_calls"][0]["function"]["arguments"] == arguments


def test_invalid_tool_arguments_can_be_archived_as_failure_instead_of_crashing(state, monkeypatch):
    from research_agent.tools import ToolRegistry, get_registry
    from research_agent.tools.builtin import register_builtins
    from research_agent.tool_context import compact_completed
    monkeypatch.setattr(ToolRegistry, "_instance", None)
    register_builtins()
    messages = [{"role": "assistant", "tool_calls": [{"id": "invalid", "type": "function",
        "function": {"name": "file_write", "arguments": "[]"}}]},
        {"role": "tool", "tool_call_id": "invalid", "content": json.dumps({"error": "Expected object"})}]
    compact_completed(messages, get_registry(), state, fold_deferred=True)
    assert json.loads(messages[0]["tool_calls"][0]["function"]["arguments"])["summary"] == {"invalid_arguments": "list"}
    assert json.loads(messages[1]["content"])["error"] == "Expected object"
