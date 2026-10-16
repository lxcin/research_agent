import json
import pytest
from research_agent.context_budget import fit, externalize, ContextBudgetExceeded
from research_agent.models import AgentState
from research_agent.tools.builtin.context_results import read_result


def test_full_tool_output_can_be_retrieved_and_is_conversation_scoped():
    state = AgentState(workspace_dir="workspace", active_chat_id="one")
    data = {"content": "valuable evidence " * 5000}
    ref = externalize(data, state, max_tokens=100)
    assert ref["_truncated"]
    parts = []
    offset = 0
    while True:
        result = read_result(dict(artifact_id=ref["artifact_id"], offset=offset, limit=6000), None, state, None)
        assert result.success
        parts.append(result.data["text"])
        if not result.data["has_more"]:
            break
        offset = result.data["next_offset"]
    assert json.loads("".join(parts)) == data
    other = AgentState(workspace_dir="workspace", active_chat_id="two")
    assert not read_result(dict(artifact_id=ref["artifact_id"]), None, other, None).success


def test_budget_preserves_constraint_and_never_slices_current_input():
    messages = [{"role": "system", "content": "identity"},
                {"role": "system", "content": "最近对话:" + "old " * 1000},
                {"role": "user", "content": "current"},
                {"role": "system", "content": "[关键约束] do not delete"}]
    selected, removed = fit(messages, [], 400, 100)
    assert removed == 1
    assert selected[-1] == messages[-1]
    assert messages[1]["content"].endswith("old ")
    with pytest.raises(ContextBudgetExceeded):
        fit([{"role": "user", "content": "required " * 1000}], [], 100, 20)


def test_multimodal_estimate_does_not_tokenize_image_base64():
    from research_agent.context_budget import estimate
    def message(payload):
        return [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": payload}}]}]
    assert estimate(message("data:" + "x" * 100000), []) == estimate(message("small"), [])
    with pytest.raises(ContextBudgetExceeded):
        fit(message("image"), [], 1000, 100)


def test_complete_old_tool_group_removed_latest_group_kept():
    from research_agent.context_budget import budget
    def group(key, text):
        return [{"role": "assistant", "tool_calls": [{"id": key, "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": key, "content": text}]
    messages = [{"role": "user", "content": "keep"}] + group("old", "large " * 1000) + group("new", "small")
    fitted, report = budget(messages, [], 500, 100, margin=20)
    assert report.removed_blocks == 1
    assert not any(m.get("tool_call_id") == "old" for m in fitted)
    assert fitted[-1]["tool_call_id"] == "new"
    assert report.input_after + report.output_reserve + report.safety_margin <= report.window


def test_incomplete_tool_protocol_is_rejected_even_with_spare_budget():
    with pytest.raises(ContextBudgetExceeded):
        fit([{"role": "tool", "tool_call_id": "orphan", "content": "x"}], [], 10000, 100)


def test_provider_receives_same_output_cap_as_budget(monkeypatch):
    from types import SimpleNamespace
    from research_agent.context_budget import output_cap
    from research_agent.model_adapter import call_with_tools, ModelSettings
    import litellm
    seen = {}
    def completion(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=None))])
    monkeypatch.setattr(litellm, "completion", completion)
    token = output_cap.set(123)
    try:
        call_with_tools(SimpleNamespace(model="custom"), [{"role": "user", "content": "hi"}], [],
                        settings=ModelSettings(0.3, 0.7, 999))
    finally:
        output_cap.reset(token)
    assert seen["max_tokens"] == 123


def test_reported_prompt_usage_calibrates_next_request(monkeypatch):
    from types import SimpleNamespace
    from research_agent.runtime import RuntimeHost, RuntimeContext
    monkeypatch.setenv("RESEARCH_AGENT_MAX_CONTEXT_TOKENS", "10000")
    events = []
    ctx = RuntimeContext(llm=SimpleNamespace(model="custom"), user_input="hi", state=AgentState(),
        call_llm_with_tools=lambda *args: {"content": "x", "usage": {"prompt_tokens": 1000}},
        emit=lambda kind, data: events.append((kind, data)))
    host = RuntimeHost(ctx)
    messages = [{"role": "user", "content": "hi"}]
    host.select(messages)
    host._budget(messages, [])
    reports = [data for kind, data in events if kind == "context_budget"]
    assert reports[1]["input_after"] >= 1000
    assert any(kind == "context_calibration" for kind, data in events)
