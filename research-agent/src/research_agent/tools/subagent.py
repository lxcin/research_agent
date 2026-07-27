"""Subagent spawn — parallel independent subtask execution."""
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from research_agent.tools.schema import ToolSchema, ToolResult

logger = logging.getLogger(__name__)

EventCallback = Callable[[str, dict], None]


def _filter_tools_dict(all_tools: dict, allowed: list[str] | None) -> dict:
    if allowed is None:
        return all_tools
    return {k: v for k, v in all_tools.items() if k in allowed}


def _build_subagent_context(
    task: str,
    parent_state,
    parent_context: str = "",
    max_tokens: int = 4000,
) -> str:
    parts = [
        "你是一个子任务代理(sub-agent)。只完成分配给你的具体任务。",
        f"任务: {task}",
    ]
    if parent_context:
        trimmed = parent_context[:max_tokens - 200]
        parts.append(f"参考上下文: {trimmed[:3000]}")
    parts.append('请返回 JSON: {"summary": "你的总结", "key_points": [...]}')
    return "\n".join(parts)


def _merge_summaries(summaries: list[dict], llm) -> str:
    if not summaries:
        return ""
    combined = "\n".join(
        f"[来源{i+1}] {s.get('summary', str(s))}"
        for i, s in enumerate(summaries)
    )
    try:
        merged = llm.complete(
            [
                {
                    "role": "user",
                    "content": (
                        "合并以下并行研究子任务的结果为一段连贯的综述，"
                        "保留关键发现和引用来源编号。\n\n" + combined
                    ),
                }
            ],
            max_tokens=2000,
        )
        return merged
    except Exception:
        return combined


def _run_single_subagent(
    task: dict,
    llm,
    state,
    max_rounds: int = 3,
    registry=None,
    locked_tool_names: list[str] | None = None,
    parent_context: str = "",
) -> dict:
    task_text = task.get("task", "")
    allowed_tools = task.get("tools", None)

    context = _build_subagent_context(task_text, state, parent_context)

    if registry:
        all_tools = registry.tools
        available = _filter_tools_dict(all_tools, allowed_tools)
    else:
        available = {}

    messages = [{"role": "system", "content": context}]
    rounds_used = 0

    for round_num in range(1, max_rounds + 1):
        rounds_used = round_num
        try:
            resp = _call_llm(llm, messages)
        except Exception as e:
            logger.warning(f"Subagent LLM call failed: {e}")
            break

        content = resp.get("content", "")
        tool_calls = resp.get("tool_calls", [])

        if not tool_calls:
            try:
                summary = json.loads(content) if content.strip().startswith("{") else {"summary": content}
            except json.JSONDecodeError:
                summary = {"summary": content}
            summary["task"] = task_text
            summary["rounds_used"] = rounds_used
            return summary

        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        for tc in tool_calls:
            name = tc.get("name", "")
            if name not in available:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": json.dumps({"error": f"Subagent cannot use tool '{name}'"}),
                })
                continue
            try:
                result = registry.dispatch(name, tc.get("params", {}), llm, state, lambda e, d: None)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": json.dumps(result.data, ensure_ascii=False),
                })
            except Exception as e:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": json.dumps({"error": str(e)}),
                })

    try:
        resp = _call_llm(llm, messages)
        content = resp.get("content", "")
        try:
            summary = json.loads(content) if content.strip().startswith("{") else {"summary": content}
        except json.JSONDecodeError:
            summary = {"summary": content}
    except Exception:
        summary = {"summary": f"Subagent ran {rounds_used} rounds but failed to produce output"}

    summary["task"] = task_text
    summary["rounds_used"] = rounds_used
    return summary


def _call_llm(llm, messages: list[dict]) -> dict:
    import litellm
    model = getattr(llm, "model", "openai/deepseek-chat")
    api_key = getattr(llm, "api_key", None)
    kwargs = getattr(llm, "_kwargs", {})
    resp = litellm.completion(
        model=model, messages=messages,
        temperature=0.3, api_key=api_key, **kwargs,
    )
    msg = resp.choices[0].message
    result = {"content": msg.content or ""}
    if msg.tool_calls:
        result["tool_calls"] = [
            {"id": tc.id, "name": tc.function.name, "params": json.loads(tc.function.arguments)}
            for tc in msg.tool_calls
        ]
    return result


def spawn_subagent(subtasks: list[dict], llm, state, registry,
                   max_rounds: int = 3, emit: EventCallback = None) -> dict:
    if not subtasks:
        return {"success": True, "completed": 0, "failed": 0, "summaries": []}

    results_lock = threading.Lock()
    summaries = []
    failed = 0

    def _emit(etype, data):
        if emit:
            emit(etype, data)

    def _worker(task: dict):
        try:
            result = _run_single_subagent(
                task, llm, state, max_rounds, registry,
                locked_tool_names=task.get("tools"),
            )
            with results_lock:
                summaries.append(result)
            _emit("subagent_done", {"task": task.get("task", "")[:60], "success": True})
            return result
        except Exception as e:
            with results_lock:
                nonlocal failed
                failed += 1
            _emit("subagent_fail", {"task": task.get("task", "")[:60], "error": str(e)})
            return None

    with ThreadPoolExecutor(max_workers=min(len(subtasks), 6)) as ex:
        futures = [ex.submit(_worker, t) for t in subtasks]
        for f in as_completed(futures):
            try:
                f.result(timeout=120)
            except Exception:
                with results_lock:
                    failed += 1

    return {
        "success": True,
        "completed": len(summaries),
        "failed": failed,
        "summaries": summaries,
    }


def _spawn_handler(params: dict, llm, state, emit: EventCallback) -> ToolResult:
    from research_agent.tools import get_registry
    registry = get_registry()

    subtasks = params.get("subtasks", params.get("tasks", []))
    if not subtasks:
        return ToolResult.fail("No subtasks provided")

    if not isinstance(subtasks, list):
        return ToolResult.fail("subtasks must be a list")

    max_rounds = int(params.get("max_rounds", 3))

    emit("step", {"step": "spawn", "text": f"启动 {len(subtasks)} 个子任务代理..."})

    result = spawn_subagent(subtasks, llm, state, registry, max_rounds, emit)

    if result["failed"] > 0:
        emit("step", {"step": "spawn_summary", "text": f"完成 {result['completed']}/{len(subtasks)} 个子任务 ({result['failed']} 失败)"})

    merged = _merge_summaries(result["summaries"], llm)

    return ToolResult.ok(
        completed=result["completed"],
        failed=result["failed"],
        summaries=result["summaries"],
        merged_text=merged,
    )


spawn_subagent_tool = ToolSchema(
    name="spawn_subagent",
    description=(
        "Spawn multiple parallel sub-agents to work on independent subtasks. "
        "Each sub-agent runs independently with restricted tool access. "
        "Use this to divide complex research into parallel work, then results are merged. "
        "Example: 'review 5 papers' → spawn 5 sub-agents each reading one paper."
    ),
    parameters={
        "type": "object",
        "properties": {
            "subtasks": {
                "type": "array",
                "description": "List of subtask definitions",
                "items": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "Task description for this sub-agent (e.g. 'Read paper X and summarize key findings')",
                        },
                        "tools": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of tool names this sub-agent is allowed to use (e.g. ['read_paper', 'retrieve']). Omit or set null for all tools.",
                        },
                    },
                    "required": ["task"],
                },
            },
            "max_rounds": {
                "type": "integer",
                "description": "Maximum agent loop rounds per sub-agent (default 3)",
            },
        },
        "required": ["subtasks"],
    },
    handler=_spawn_handler,
    category="builtin",
)
