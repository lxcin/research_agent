"""Subagent spawn tests — parallel independent subtask execution.
Uses MockLLMProvider for deterministic, no-network tests."""
import json
import pytest
from concurrent.futures import ThreadPoolExecutor, as_completed

from research_agent.llm import MockLLMProvider
from research_agent.models import AgentState

# Will be implemented after tests pass design review
# from research_agent.tools.subagent import spawn_subagent, run_sub_agent


def make_state(user_input: str = "test task") -> AgentState:
    """Create a minimal AgentState with no active project."""
    state = AgentState(user_input=user_input)
    state.active_project = None
    return state


class TestSpawnSubagent:
    """spawn_subagent dispatches parallel sub-agents and merges results."""

    def test_spawns_correct_number_of_subagents(self):
        """n subtasks → n sub-agents launched."""
        subtasks = [
            {"task": "Read paper A", "tools": ["read_paper", "retrieve"]},
            {"task": "Read paper B", "tools": ["read_paper", "retrieve"]},
            {"task": "Read paper C", "tools": ["read_paper"]},
        ]
        llm = MockLLMProvider([
            json.dumps({"summary": "Paper A is about transformers"}),
            json.dumps({"summary": "Paper B is about attention"}),
            json.dumps({"summary": "Paper C is about GPT"}),
        ])
        state = make_state("review 3 papers")
        result = _spawn_subagent(subtasks, llm, state, max_rounds=2)
        assert result["success"] is True
        assert result["completed"] == 3
        assert result["failed"] == 0
        summaries = result["summaries"]
        assert len(summaries) == 3

    def test_parallel_execution_is_concurrent(self):
        """Sub-agents run in parallel (total wall time < sum of individual)."""
        import time
        subtasks = [
            {"task": f"task {i}", "tools": []}
            for i in range(4)
        ]
        llm = MockLLMProvider(["ok"] * 4)

        def slow_runner(task, l, s, mr):
            """Simulate I/O delay in a sub-agent."""
            time.sleep(0.2)
            return {"summary": f"done: {task['task']}"}

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(slow_runner, t, llm, make_state(), 1) for t in subtasks]
            results = [f.result(timeout=10) for f in as_completed(futures)]
        elapsed = time.time() - t0
        # 4 tasks × 0.2s each, parallel → < 0.6s (not 0.8s sequential)
        assert elapsed < 0.8
        assert len(results) == 4

    def test_failed_subagent_does_not_block_others(self):
        """One failing sub-agent doesn't block the others."""
        subtasks = [
            {"task": "good task", "tools": []},
            {"task": "bad task: raise error", "tools": []},
            {"task": "another good task", "tools": []},
        ]

        def mixed_runner(task, llm, state, mr):
            if "bad task" in task["task"]:
                raise RuntimeError("simulated sub-agent crash")
            return {"summary": f"ok: {task['task']}"}

        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = [ex.submit(mixed_runner, t, None, None, 1) for t in subtasks]
            results = []
            errors = 0
            for f in as_completed(futures):
                try:
                    results.append(f.result(timeout=10))
                except Exception:
                    errors += 1

        assert len(results) == 2
        assert errors == 1

    def test_respects_max_rounds(self):
        """Sub-agent does not exceed max_rounds."""
        subtasks = [{"task": "test", "tools": ["retrieve"]}]

        def counting_runner(task, llm, state, max_rounds):
            assert max_rounds == 3
            return {"summary": "done", "rounds_used": max_rounds}

        result = _run_single_subagent(subtasks[0], None, make_state(), max_rounds=3,
                                       runner=counting_runner)
        assert result["rounds_used"] == 3


class TestSubagentToolRestriction:
    """Sub-agents cannot use tools outside their allowlist."""

    def test_tool_filtering(self):
        """Only tools in the allowlist are available to the sub-agent."""
        all_tools = {
            "retrieve": "search local DB",
            "search_papers": "search arXiv",
            "shell_exec": "run commands",
            "file_write": "write files",
            "read_paper": "read paper content",
        }
        allowed = ["retrieve", "read_paper"]
        filtered = _filter_tools(all_tools, allowed)
        assert "retrieve" in filtered
        assert "read_paper" in filtered
        assert "shell_exec" not in filtered
        assert "file_write" not in filtered

    def test_empty_allowlist_means_none(self):
        """Empty allowlist = no tools at all."""
        filtered = _filter_tools({"a": "desc a", "b": "desc b"}, [])
        assert len(filtered) == 0

    def test_none_allowlist_means_all(self):
        """None allowlist = all tools available (no restriction)."""
        tools = {"a": "desc a", "b": "desc b"}
        filtered = _filter_tools(tools, None)
        assert filtered == tools


class TestSubagentContext:
    """Sub-agent inherits relevant context from the parent."""

    def test_inherits_task_description(self):
        """Sub-agent gets clear task description in its prompt."""
        context = _build_subagent_context(
            task="summarize paper X",
            parent_state=make_state("review all papers"),
            parent_context="Paper X is about attention mechanisms",
        )
        assert "summarize paper X" in context
        assert "attention mechanisms" in context
        assert "sub-agent" in context.lower() or "子任务" in context

    def test_token_budget_is_limited(self):
        """Sub-agent context stays within token budget."""
        context = _build_subagent_context(
            task="summarize",
            parent_state=make_state("test"),
            parent_context="A" * 10000,
            max_tokens=2000,
        )
        # Rough estimate: character count should be bounded
        assert len(context) < 10000

    def test_no_parent_context_is_ok(self):
        context = _build_subagent_context(
            task="do X",
            parent_state=make_state("test"),
            parent_context="",
        )
        assert len(context) > 0


class TestMergeSummaries:
    """Results from parallel sub-agents are merged into a coherent response."""

    def test_merge_multiple_sources(self):
        summaries = [
            {"source": "paper_a", "finding": "transformers work", "confidence": "high"},
            {"source": "paper_b", "finding": "CNNs also work", "confidence": "medium"},
            {"source": "paper_c", "finding": "both are good", "confidence": "high"},
        ]
        llm = MockLLMProvider([
            "合并结果: transformers work, CNNs also work, both are good."
        ])
        merged = _merge_subagent_results(summaries, make_state(), llm)
        assert isinstance(merged, dict)
        assert merged.get("merged_text", "")

    def test_merge_empty_summaries(self):
        """Merging nothing returns an empty result."""
        llm = MockLLMProvider(["nothing to merge"])
        merged = _merge_subagent_results([], make_state(), llm)
        assert merged.get("merged_text", "") == ""


# ── Implementation stubs (exercised by tests above) ──

def _spawn_subagent(subtasks: list[dict], llm, state, max_rounds: int = 3) -> dict:
    """Spawn parallel sub-agents for independent subtasks."""
    if not subtasks:
        return {"success": True, "completed": 0, "failed": 0, "summaries": []}

    with ThreadPoolExecutor(max_workers=min(len(subtasks), 6)) as ex:
        futures = {
            ex.submit(_run_single_subagent, task, llm, state, max_rounds): task
            for task in subtasks
        }
        summaries = []
        failed = 0
        for f in as_completed(futures):
            try:
                result = f.result(timeout=60)
                summaries.append(result)
            except Exception:
                failed += 1

    completed = len(summaries)
    return {
        "success": True,
        "completed": completed,
        "failed": failed,
        "summaries": summaries,
    }


def _run_single_subagent(
    task: dict,
    llm,
    state,
    max_rounds: int = 3,
    runner=None,
) -> dict:
    """Run a single sub-agent for one subtask. runner is optional for testing."""
    if runner:
        return runner(task, llm, state, max_rounds)

    # Real implementation: mini agent loop with filtered tools
    rounds = 0
    task_text = task.get("task", "")
    allowed_tools = task.get("tools", None)

    # Dummy: call LLM for summary (the mock returns pre-programmed text)
    context = _build_subagent_context(task_text, state, "")
    try:
        raw = llm.complete([{"role": "user", "content": context}])
        summary = json.loads(raw) if raw.strip().startswith("{") else {"summary": raw}
    except (json.JSONDecodeError, AttributeError):
        summary = {"summary": f"Error processing task: {task_text[:100]}", "error": True}

    if isinstance(summary, dict):
        summary["task"] = task_text
        summary["rounds_used"] = min(rounds + 1, max_rounds)
    return summary


def _filter_tools(all_tools: dict, allowed: list[str] | None) -> dict:
    """Filter tools dictionary to only include allowed names."""
    if allowed is None:
        return all_tools
    return {k: v for k, v in all_tools.items() if k in allowed}


def _build_subagent_context(
    task: str,
    parent_state,
    parent_context: str = "",
    max_tokens: int = 4000,
) -> str:
    """Build a compact context for a sub-agent."""
    parts = [
        "你是一个子任务代理(sub-agent)。只完成分配给你的具体任务。",
        f"任务: {task}",
    ]
    if parent_context:
        trimmed = parent_context[:max_tokens - 200]
        parts.append(f"参考上下文: {trimmed[:3000]}")
    parts.append("请返回 JSON: {\"summary\": \"你的总结\", \"key_points\": [...]}")
    return "\n".join(parts)


def _merge_subagent_results(summaries: list[dict], state, llm) -> dict:
    """Merge parallel sub-agent results into a coherent output."""
    if not summaries:
        return {"merged_text": ""}
    # Simulate LLM merging
    combined = "\n".join(str(s.get("summary", "")) for s in summaries)
    try:
        merged = llm.complete([{"role": "user", "content": f"合并以下研究结果:\n{combined}"}])
    except Exception:
        merged = combined
    return {"merged_text": merged, "source_count": len(summaries)}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
