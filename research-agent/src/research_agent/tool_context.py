"""Host projection of completed exchanges, with policies owned by tools."""
import json

from research_agent.context_budget import artifact_directory, store_artifact


def _brief(params):
    return {key: (value if not isinstance(value, (dict, list, str)) else
                  value if isinstance(value, str) and len(value) <= 160 else
                  {"stored": True, "items": len(value)}) for key, value in params.items()}


def compact_completed(messages, registry, state, *, fold_deferred=False):
    """Mutate completed history only; pending execution arguments remain intact.

    The latest read result stays available for the next model decision. When a
    newer tool group appears, its declaring tool may replace it with a summary.
    """
    tools = getattr(registry, "tools", {})
    if "context_result_read" not in tools:
        return
    groups = [(i, msg) for i, msg in enumerate(messages) if msg.get("tool_calls")]
    if not groups:
        return
    latest = groups[-1][0]
    results = {msg.get("tool_call_id"): msg for msg in messages if msg.get("role") == "tool"}
    for index, group in groups:
        for call in group["tool_calls"]:
            if call["id"] not in results:
                continue
            function = call["function"]
            spec = tools.get(function["name"])
            params = json.loads(function["arguments"])
            if getattr(spec, "defer_context", False) and not fold_deferred:
                if not (isinstance(params, dict) and set(params) == {"_stored_call", "summary"}):
                    store_artifact({"tool": function["name"], "params": params}, state)
                continue
            if not (isinstance(params, dict) and set(params) == {"_stored_call", "summary"}):
                key = store_artifact({"tool": function["name"], "params": params}, state)
                projector = getattr(spec, "context_call", None)
                summary = (projector(params) if projector else _brief(params)) if isinstance(params, dict) else {
                    "invalid_arguments": type(params).__name__}
                function["arguments"] = json.dumps({"_stored_call": key, "summary": summary}, ensure_ascii=False)
            projector = getattr(spec, "history_result", None)
            if (index == latest and not fold_deferred) or projector is None:
                continue
            result = results[call["id"]]
            data = json.loads(result["content"])
            if not isinstance(data, dict):
                continue
            if data.get("_history_summary"):
                continue
            key = data.get("_tool_result_id") or data.get("artifact_id")
            if key:
                import re
                if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
                    raise ValueError("Invalid tool result artifact reference")
                path = artifact_directory(state) / (key + ".json")
                if path.is_file():
                    data = json.loads(path.read_text(encoding="utf-8"))
            else:
                key = store_artifact(data, state)
            result["content"] = json.dumps(dict(projector(data), _tool_result_id=key,
                _history_summary=True), ensure_ascii=False)


def append_probe_notices(messages, registry, state):
    """Invoke capability-owned probes once each; only append their notices."""
    seen, appended = set(), 0
    for spec in getattr(registry, "tools", {}).values():
        probe = getattr(spec, "context_probe", None)
        if probe is None or probe in seen:
            continue
        seen.add(probe)
        notice = probe(state)
        if notice:
            messages.append({"role": "system", "content": notice})
            appended += 1
    return appended


def fold_at_watermark(messages, registry, state, tools, *, window, reserve, margin, counter=None, image_tokens=4096, config=None):
    """One deterministic batch; no per-edit or per-read historical rewriting."""
    from copy import deepcopy
    from research_agent.context_budget import estimate
    cfg = config or {}
    soft = float(cfg.get("soft_watermark", 0.8))
    minimum = int(cfg.get("min_fold_tokens", 512))
    before = estimate(messages, tools, counter, image_tokens)
    available = window - reserve - margin
    if before <= available * soft:
        return 0
    candidate = deepcopy(messages)
    compact_completed(candidate, registry, state, fold_deferred=True)
    saved = before - estimate(candidate, tools, counter, image_tokens)
    if saved > 0 and (saved >= minimum or before > available):
        messages[:] = candidate
        return saved
    return 0
