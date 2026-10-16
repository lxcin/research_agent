"""Conservative request budgeting and retrievable full tool outputs."""
import hashlib
import json
from pathlib import Path
from research_agent.context import count_tokens


class ContextBudgetExceeded(BaseException):
    pass


def artifact_directory(state):
    from research_agent.config import get_data_dir
    identity = json.dumps([getattr(state, "workspace_dir", ""), getattr(state, "active_chat_id", "")])
    return get_data_dir() / "context_artifacts" / hashlib.sha256(identity.encode()).hexdigest()


def store_artifact(data, state):
    raw = json.dumps(data, ensure_ascii=False)
    key = hashlib.sha256(raw.encode()).hexdigest()
    directory = artifact_directory(state)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (key + ".json")
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == key:
        return key
    import os, tempfile
    descriptor, temporary = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return key


def externalize(data, state, max_tokens=2000, *, always_store=False):
    raw = json.dumps(data, ensure_ascii=False)
    large = count_tokens(raw) > max_tokens
    if not large and not always_store:
        return data
    key = store_artifact(data, state)
    if not large:
        return dict(data, _tool_result_id=key)
    preview = raw[:1000]
    return {"_truncated": True, "artifact_id": key, "_tool_result_id": key, "characters": len(raw), "preview": preview,
            "instruction": "完整结果已外置；使用 context_result_read(artifact_id, offset, limit) 分段读取，不应根据预览推断完整内容。",
            **{k: (data[k][:500] if isinstance(data[k], str) else data[k])
               for k in ("error", "success", "returncode", "path", "completed", "verification", "operation", "changed",
                         "before_sha256", "after_sha256", "sha256", "version", "expected_version", "hash_before", "hash_after",
                         "error_code", "status", "instruction", "match_count", "diff_ref", "offset", "next_offset", "has_more", "start_line", "end_line")
               if k in data and isinstance(data[k], (str, bool, int))}}


from dataclasses import dataclass, asdict
from contextvars import ContextVar

output_cap = ContextVar("model_output_cap", default=None)


@dataclass(frozen=True)
class BudgetReport:
    window: int
    output_reserve: int
    safety_margin: int
    input_before: int
    input_after: int
    removed_blocks: int
    counter: str


def estimate(messages, tools, counter=None, image_tokens=4096):
    if counter is not None:
        value = counter(messages, tools)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("Token counter must return a nonnegative integer")
        return value
    import copy
    normalized = copy.deepcopy(messages)
    images = 0
    for message in normalized:
        content = message.get("content")
        if isinstance(content, list):
            blocks = []
            for block in content:
                if block.get("type") == "image_url":
                    images += 1
                    blocks.append({"type": "text", "text": "[image]"})
                elif block.get("type") == "text":
                    blocks.append(block)
                else:
                    raise ContextBudgetExceeded("Unsupported modality; configure an adapter token counter")
            message["content"] = blocks
    return count_tokens(json.dumps({"messages": normalized, "tools": tools}, ensure_ascii=False)) + images * image_tokens + 8 * len(messages)


def budget(messages, tools, window, reserve, *, margin=0, counter=None, image_tokens=4096):
    if window <= 0 or reserve <= 0 or margin < 0 or image_tokens <= 0 or window <= reserve + margin:
        raise ContextBudgetExceeded("Invalid context window/output reserve/safety margin")
    pending = set()
    for message in messages:
        if message.get("tool_calls"):
            if pending:
                raise ContextBudgetExceeded("Incomplete tool exchange before next assistant")
            pending = {call["id"] for call in message["tool_calls"]}
        elif message.get("role") == "tool":
            key = message.get("tool_call_id")
            if key not in pending:
                raise ContextBudgetExceeded("Orphan or duplicate tool result")
            pending.remove(key)
        elif pending and message.get("role") in ("assistant", "user"):
            raise ContextBudgetExceeded("Incomplete tool exchange")
    if pending:
        raise ContextBudgetExceeded("Incomplete final tool exchange")
    before = estimate(messages, tools, counter, image_tokens)
    result = list(messages)
    removed = 0
    while estimate(result, tools, counter, image_tokens) + reserve + margin > window:
        history = next((i for i, m in enumerate(result) if m.get("role") == "system" and
                        isinstance(m.get("content"), str) and m["content"].startswith(
                            ("最近对话:", "历史摘要/结论:", "已验证不可行的方向"))), None)
        if history is not None:
            result.pop(history)
            removed += 1
            continue
        candidates = [i for i, m in enumerate(result) if m.get("tool_calls")]
        if len(candidates) < 2:
            raise ContextBudgetExceeded(f"Mandatory context exceeds budget: input={estimate(result, tools, counter, image_tokens)}, reserve={reserve}, margin={margin}, window={window}")
        start, stop = candidates[0], candidates[1]
        ids = {call["id"] for call in result[start]["tool_calls"]}
        seen = {m.get("tool_call_id") for m in result[start + 1:stop] if m.get("role") == "tool"}
        if not ids.issubset(seen):
            raise ContextBudgetExceeded("Cannot evict incomplete tool exchange")
        result = result[:start] + [m for m in result[start + 1:stop] if m.get("role") != "tool"] + result[stop:]
        removed += 1
    report = BudgetReport(window, reserve, margin, before, estimate(result, tools, counter, image_tokens),
                          removed, "adapter" if counter else "cl100k_estimate")
    return result, report


def fit(messages, tools, limit, reserve=4096):
    fitted, report = budget(messages, tools, limit, reserve)
    return fitted, report.removed_blocks
