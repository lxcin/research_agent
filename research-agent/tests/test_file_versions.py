"""Version checks, delivered coverage and bounded source reads across sessions."""
import hashlib
import json
from pathlib import Path

import pytest

from research_agent.models import AgentState
from research_agent import file_views as fv
from research_agent.tools.builtin.filesystem import (
    _handle_file_read as read, _handle_file_edit as edit, _handle_file_write as write,
    _probe_files, _grep_python,
)


@pytest.fixture
def state(tmp_path):
    return AgentState(workspace_dir=str(tmp_path), active_chat_id="one")


def call(tool, state, **params):
    return tool(params, None, state, lambda *a: None)


def test_unchanged_version_allows_edit_without_rereading(state):
    path = Path(state.workspace_dir) / "code.py"
    path.write_bytes(b"before\ntarget\nafter\n")
    observed = call(read, state, path="code.py")
    version = observed.data["version"]
    result = call(edit, state, path="code.py", old_string="target", new_string="updated", expected_version=version)
    assert result.success and result.data["hash_before"] == version
    assert "-target" in result.data["diff"] and "+updated" in result.data["diff"]
    assert result.data["version"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result.data["diff_ref"]
    # The generated changed text is known on the new version.
    assert call(edit, state, path="code.py", old_string="updated", new_string="again",
                expected_version=result.data["version"]).success


def test_human_edit_rejects_old_version_even_when_target_did_not_change(state):
    path = Path(state.workspace_dir) / "code.py"
    path.write_bytes(b"target\nold other\n")
    version = call(read, state, path="code.py").data["version"]
    path.write_bytes(b"target\nhuman other\n")
    result = call(edit, state, path="code.py", old_string="target", new_string="bad", expected_version=version)
    assert not result.success and result.data["error_code"] == "FILE_CHANGED"
    assert path.read_bytes() == b"target\nhuman other\n"
    assert next(iter(fv.views(state).values()))["stale"]


def test_probe_only_notifies_and_does_not_restore_source_or_read_version(state):
    path = Path(state.workspace_dir) / "code.py"
    path.write_bytes(b"original secret")
    version = call(read, state, path="code.py").data["version"]
    path.write_bytes(b"human secret")
    notice = _probe_files(state)
    assert "code.py" in notice and "human secret" not in notice and "original secret" not in notice
    record = next(iter(fv.views(state).values()))
    assert record["version"] == version and record["disk_version"] != version and record["stale"]
    assert _probe_files(state) == ""
    call(read, state, path="code.py")
    assert not next(iter(fv.views(state).values()))["stale"]


def test_read_coverage_is_version_and_session_scoped(state):
    path = Path(state.workspace_dir) / "code.py"
    path.write_bytes(b"first\nsecond\nthird\n")
    result = call(read, state, path="code.py", start_line=1, end_line=1)
    version = result.data["version"]
    denied = call(edit, state, path="code.py", old_string="third", new_string="x", expected_version=version)
    assert denied.data["error_code"] == "NOT_READ"
    other = AgentState(workspace_dir=state.workspace_dir, active_chat_id="two")
    other.components.inject(fv.FILE_VIEWS)
    other.components.set(fv.FILE_VIEWS.key, fv.views(state))
    assert call(edit, other, path="code.py", old_string="first", new_string="x",
                expected_version=version).data["error_code"] == "NOT_READ"
    assert call(write, state, path="code.py", content="overwrite", expected_version=version).data["error_code"] == "NOT_READ"
    call(read, state, path="code.py", start_line=2)
    assert call(write, state, path="code.py", content="overwrite", expected_version=version).success


def test_coverage_preserves_gaps_and_merges_adjacent_ranges(state):
    (Path(state.workspace_dir) / "code.py").write_bytes(b"one\ntwo\nthree\n")
    call(read, state, path="code.py", start_line=1, end_line=1)
    result = call(read, state, path="code.py", start_line=3)
    assert len(result.data["read_coverage"]) == 2
    result = call(read, state, path="code.py", start_line=2, end_line=2)
    assert len(result.data["read_coverage"]) == 1


def test_creation_requires_absence_and_never_overwrites_existing(state):
    assert call(write, state, path="new.py", content="x").data["error_code"] == "VERSION_REQUIRED"
    assert call(write, state, path="new.py", content="x", expected_version="absent").success
    result = call(write, state, path="new.py", content="bad", expected_version="absent")
    assert not result.success and result.data["error_code"] == "FILE_CHANGED"
    assert (Path(state.workspace_dir) / "new.py").read_text() == "x"


def test_unicode_long_line_pagination_has_no_gaps_or_partial_characters(state):
    text = "短行\r\n" + "长🙂" * 10000 + "\r\n尾行\n"
    (Path(state.workspace_dir) / "long.txt").write_bytes(text.encode())
    cursor, pieces, seen_truncation = {"line": 1, "column": 1}, [], False
    while True:
        result = call(read, state, path="long.txt", start_line=cursor["line"],
                      start_column=cursor["column"], limit=137)
        assert result.success
        data = result.data
        pieces.append(data["content"])
        assert data["range_hash"] == hashlib.sha256(data["content"].encode()).hexdigest()
        assert len(data["content"]) <= 138
        seen_truncation |= bool(data["truncated_lines"])
        if not data["has_more"]:
            break
        assert data["next_cursor"] != cursor
        cursor = data["next_cursor"]
    assert "".join(pieces) == text and seen_truncation


def test_normal_page_does_not_cut_short_lines(state):
    (Path(state.workspace_dir) / "short.txt").write_bytes(b"abc\ndef\nghi\n")
    result = call(read, state, path="short.txt", limit=6)
    assert result.data["content"] == "abc\n"
    assert result.data["next_cursor"] == {"line": 2, "column": 1}
    assert result.data["truncated_lines"] == []


def test_single_character_limit_handles_crlf_without_livelock(state):
    (Path(state.workspace_dir) / "short.txt").write_bytes(b"\r\nx")
    result = call(read, state, path="short.txt", limit=1)
    assert result.data["content"] == "\r\n"
    assert result.data["next_cursor"] == {"line": 2, "column": 1}


@pytest.mark.parametrize("raw,code", [(b"a\0b", "BINARY_FILE"), (b"\xff\xfeA", "UNSUPPORTED_ENCODING")])
def test_unsupported_content_returns_metadata_without_edit_qualification(state, raw, code):
    (Path(state.workspace_dir) / "binary").write_bytes(raw)
    result = call(read, state, path="binary")
    assert not result.success and result.data["error_code"] == code
    assert result.data["version"] == hashlib.sha256(raw).hexdigest()
    assert "content" not in result.data and not fv.views(state)


def test_large_file_read_streams_without_path_read_bytes(state, monkeypatch):
    path = Path(state.workspace_dir) / "big.py"
    path.write_bytes(b"line\n" * 400000)
    monkeypatch.setattr(Path, "read_bytes", lambda *a: pytest.fail("must stream"))
    result = call(read, state, path="big.py", start_line=200000, end_line=200002)
    assert result.success and result.data["content"] == "line\n" * 3
    assert result.data["total_lines"] == 400000


def test_second_check_catches_change_before_replace(state, monkeypatch):
    path = Path(state.workspace_dir) / "code.py"
    path.write_bytes(b"old")
    version = call(read, state, path="code.py").data["version"]
    real = fv.fingerprint
    def race(target):
        path.write_bytes(b"human")
        return real(target)
    monkeypatch.setattr(fv, "fingerprint", race)
    result = call(edit, state, path="code.py", old_string="old", new_string="new", expected_version=version)
    assert result.data["error_code"] == "FILE_CHANGED" and path.read_bytes() == b"human"
    assert not list(path.parent.glob(".agent-edit-*"))


def test_search_fallback_finds_large_file_and_bounds_giant_line(state):
    path = Path(state.workspace_dir) / "big.py"
    path.write_text("x" * 1100000 + "\ndef target():\n", encoding="utf-8")
    matches = _grep_python(state.workspace_dir, "def target", "*.py")
    assert matches[0]["line"] == 2 and "target" in matches[0]["content"]


def test_delivered_preview_does_not_grant_read_coverage(state, monkeypatch):
    from research_agent.tools import ToolRegistry
    from research_agent.tools.builtin import _plugins
    from research_agent.runtime import RuntimeHost, RuntimeContext
    from research_agent.kernel import ToolCall
    path = Path(state.workspace_dir) / "code.py"
    path.write_text("target\n" * 1000)
    registry = ToolRegistry()
    registry.install_plugin(_plugins()[0])
    registry.bind_state(state)
    monkeypatch.setattr("research_agent.config.get_context_config", lambda: {"tool_result_tokens": 128})
    host = RuntimeHost(RuntimeContext(None, "read", state, registry=registry, emit=lambda *a: None))
    result = host.execute(ToolCall("r", "file_read", {"path": "code.py"}))
    assert result.data["_truncated"]
    assert next(iter(fv.views(state).values()))["coverage"] == []


def test_version_state_roundtrips_for_execution_recovery(state):
    (Path(state.workspace_dir) / "code.py").write_bytes(b"old")
    version = call(read, state, path="code.py").data["version"]
    saved = state.components.snapshot()
    restored = AgentState(workspace_dir=state.workspace_dir, active_chat_id="one")
    restored.components.inject(fv.FILE_VIEWS)
    restored.components.restore_snapshot(saved)
    assert call(edit, restored, path="code.py", old_string="old", new_string="new", expected_version=version).success


def _host(state):
    from research_agent.tools import ToolRegistry
    from research_agent.tools.builtin import _plugins
    from research_agent.runtime import RuntimeHost, RuntimeContext
    registry = ToolRegistry()
    registry.install_plugin(_plugins()[0])
    registry.bind_state(state)
    return RuntimeHost(RuntimeContext(None, "read", state, registry=registry, emit=lambda *a: None))


def _read_messages(host, content):
    from research_agent.kernel import ToolCall
    path = Path(host.ctx.state.workspace_dir) / "code.py"
    path.write_text(content, encoding="utf-8")
    result = host.execute(ToolCall("r", "file_read", {"path": "code.py", "limit": 16000}))
    assert result.success
    return [{"role": "assistant", "tool_calls": [{"id": "r", "type": "function", "function": {
        "name": "file_read", "arguments": json.dumps({"path": "code.py"})}}]},
        {"role": "tool", "tool_call_id": "r", "content": json.dumps(result.data)}]


def test_read_history_is_stable_until_batch_watermark_and_fold_is_deterministic(state):
    from copy import deepcopy
    from research_agent.tool_context import compact_completed, fold_at_watermark
    from research_agent.context_budget import estimate
    host = _host(state)
    messages = _read_messages(host, "source value\n" * 80)
    before = deepcopy(messages)
    compact_completed(messages, host.ctx.registry, state)
    assert messages == before
    tokens = estimate(messages, [])
    saved = fold_at_watermark(messages, host.ctx.registry, state, [], window=tokens + 256,
        reserve=128, margin=0, config={"soft_watermark": 0.5, "min_fold_tokens": 1})
    assert saved > 0
    folded = deepcopy(messages)
    assert "content" not in json.loads(messages[1]["content"])
    compact_completed(messages, host.ctx.registry, state, fold_deferred=True)
    assert messages == folded


def test_small_saving_does_not_rewrite_prefix(state):
    from copy import deepcopy
    from research_agent.tool_context import fold_at_watermark
    from research_agent.context_budget import estimate
    host = _host(state)
    messages = _read_messages(host, "source\n" * 20)
    before = deepcopy(messages)
    saved = fold_at_watermark(messages, host.ctx.registry, state, [], window=estimate(messages, []) + 500,
        reserve=128, margin=0, config={"soft_watermark": 0.1, "min_fold_tokens": 10000})
    assert saved == 0 and messages == before


def test_resume_boundary_marks_changed_read_and_does_not_reinject_old_source(state):
    host = _host(state)
    state._execution_id, state._kernel_id = "read-boundary", "function_calling"
    messages = _read_messages(host, "OLD_SOURCE_SECRET")
    host.checkpoint({"messages": messages})
    (Path(state.workspace_dir) / "code.py").write_bytes(b"NEW_HUMAN_SECRET")
    saved = _host(state).checkpoint()
    assert saved
    result = json.loads(next(m["content"] for m in saved["messages"] if m.get("role") == "tool"))
    assert "content" not in result and result["_history_summary"]
    assert "OLD_SOURCE_SECRET" not in json.dumps(saved["messages"])
    assert "NEW_HUMAN_SECRET" not in json.dumps(saved["messages"])
    assert "已过期" in saved["messages"][-1]["content"]


def test_read_rejects_out_of_bounds_column_instead_of_skipping_to_next_line(state):
    (Path(state.workspace_dir) / "code.py").write_bytes(b"a\nb\n")
    result = call(read, state, path="code.py", start_column=50)
    assert not result.success and result.data["error_code"] == "INVALID_RANGE"


def test_file_identity_change_during_scan_is_rejected(state, monkeypatch):
    from types import SimpleNamespace
    path = Path(state.workspace_dir) / "code.py"
    path.write_bytes(b"source")
    actual = fv.os.fstat
    calls = []
    def modified(descriptor):
        value = actual(descriptor)
        calls.append(1)
        return SimpleNamespace(st_dev=value.st_dev, st_ino=value.st_ino,
            st_size=value.st_size, st_mtime_ns=value.st_mtime_ns + int(len(calls) > 1), st_ctime_ns=value.st_ctime_ns)
    monkeypatch.setattr(fv.os, "fstat", modified)
    result = call(read, state, path="code.py")
    assert not result.success and result.data["error_code"] == "FILE_CHANGED_DURING_READ"
    assert not fv.views(state)


def test_completed_read_replay_is_only_metadata_after_external_change(state):
    from research_agent.tools.builtin.filesystem import _read_delivery
    path = Path(state.workspace_dir) / "code.py"
    path.write_bytes(b"OLD_SOURCE_SECRET")
    recorded = call(read, state, path="code.py").data
    path.write_bytes(b"NEW_SOURCE_SECRET")
    # Metadata probes intentionally do not hash; make the fixture's mtime
    # change explicit instead of relying on the filesystem clock resolution.
    import os
    stamp = path.stat()
    os.utime(path, ns=(stamp.st_atime_ns, recorded["file_stamp"][3] + 1_000_000_000))
    visible = dict(recorded, _tool_result_id="a" * 64)
    _read_delivery(state, recorded, visible)
    assert visible["stale"] and visible["error_code"] == "FILE_CHANGED"
    assert "content" not in visible
    assert "OLD_SOURCE_SECRET" not in json.dumps(visible) and "NEW_SOURCE_SECRET" not in json.dumps(visible)
    assert next(iter(fv.views(state).values()))["stale"]


def test_noop_edit_keeps_coverage_and_diff_marks_missing_final_newline(state):
    (Path(state.workspace_dir) / "code.py").write_bytes(b"old")
    version = call(read, state, path="code.py").data["version"]
    assert call(edit, state, path="code.py", old_string="old", new_string="old", expected_version=version).success
    result = call(edit, state, path="code.py", old_string="old", new_string="new", expected_version=version)
    assert result.success and "-old\n" in result.data["diff"] and "+new\n" in result.data["diff"]
    assert "No newline at end of file" in result.data["diff"]


def test_lone_carriage_return_is_data_and_never_creates_empty_page(state):
    (Path(state.workspace_dir) / "code.py").write_bytes(b"\rx")
    first = call(read, state, path="code.py", limit=1)
    assert first.success and first.data["content"] == "\r"
    assert first.data["next_cursor"] == {"line": 1, "column": 2}
    second = call(read, state, path="code.py", start_column=2, limit=1)
    assert second.data["content"] == "x" and not second.data["has_more"]


def test_old_character_offset_is_rejected_instead_of_silently_reading_first_page(state):
    host = _host(state)
    from research_agent.kernel import ToolCall
    (Path(state.workspace_dir) / "code.py").write_bytes(b"source")
    result = host.execute(ToolCall("old-offset", "file_read", {"path": "code.py", "offset": 20}))
    assert not result.success
    assert "offset" in result.data["error"]
    assert not fv.views(state)


def test_fresh_read_does_not_hash_file_twice(state, monkeypatch):
    host = _host(state)
    monkeypatch.setattr(fv, "fingerprint", lambda *a: pytest.fail("fresh read must use its single scan"))
    messages = _read_messages(host, "source")
    assert json.loads(messages[1]["content"])["content"] == "source"


@pytest.mark.parametrize("mode,code", [("changed", "FILE_CHANGED"), ("missing", "NO_MATCH"), ("duplicate", "MULTI_MATCH")])
def test_edit_rejection_explains_targeted_recovery_without_writing(state, mode, code):
    path = Path(state.workspace_dir) / "code.py"
    path.write_bytes(b"target\ntarget\n" if mode == "duplicate" else b"target\n")
    version = call(read, state, path="code.py").data["version"]
    if mode == "changed":
        path.write_bytes(b"human\ntarget\n")
    before = path.read_bytes()
    result = call(edit, state, path="code.py", old_string="missing" if mode == "missing" else "target",
                  new_string="replacement", expected_version=version)
    assert not result.success and not result.data["completed"]
    assert result.data["error_code"] == code and "范围" in result.data["instruction"]
    assert path.read_bytes() == before
    if mode != "changed":
        assert result.data["match_count"] == (0 if mode == "missing" else 2)


def test_overlapping_matches_are_ambiguous_and_never_written(state):
    path = Path(state.workspace_dir) / "code.py"
    path.write_bytes(b"aaaa")
    version = call(read, state, path="code.py").data["version"]
    result = call(edit, state, path="code.py", old_string="aaa", new_string="x", expected_version=version)
    assert not result.success and result.data["error_code"] == "MULTI_MATCH"
    assert result.data["match_count_is_lower_bound"] and path.read_bytes() == b"aaaa"
