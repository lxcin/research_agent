"""ToolRouter tests — intent classification, tool subsetting, confusion detection."""
import pytest

# Will use the real implementation
try:
    from research_agent.tools.router import (
        categorize_intent, route_tools, audit_tool_confusion,
        compute_similarity_scores, add_anti_confusion_hints, INTENT_ROUTES,
    )
    from research_agent.tools import get_registry
    from research_agent.tools.builtin import register_builtins
    IMPORTS_OK = True
except ImportError:
    IMPORTS_OK = False


@pytest.mark.skipif(not IMPORTS_OK, reason="router imports require research_agent")
class TestIntentClassification:

    def test_retrieve_intent(self):
        assert categorize_intent("检索注意力机制的论文") == "retrieve"
        assert categorize_intent("find papers about transformers") == "retrieve"

    def test_read_intent(self):
        assert categorize_intent("读一下这篇论文") == "read"
        assert categorize_intent("read the paper content") == "read"

    def test_search_intent(self):
        assert categorize_intent("在 arxiv 上搜索论文") == "search"
        assert categorize_intent("search for paper") == "search"

    def test_write_intent(self):
        assert categorize_intent("写一个实验脚本") == "write"
        assert categorize_intent("保存笔记") == "write"

    def test_execute_intent(self):
        assert categorize_intent("运行 python train.py") == "execute"
        assert categorize_intent("run experiment") == "execute"

    def test_git_intent(self):
        assert categorize_intent("回滚到上一个检查点") == "git"
        assert categorize_intent("git checkpoint") == "git"

    def test_review_intent(self):
        assert categorize_intent("综述 transformer 的最新进展") == "review"
        assert categorize_intent("系统性地比较 GPT 和 BERT") == "review"

    def test_default_fallback(self):
        assert categorize_intent("hello") == "default"
        assert categorize_intent("") == "default"

    def test_review_wins_ties(self):
        """Review should win when both review and search keywords appear."""
        result = categorize_intent("综述并搜索论文")
        assert result == "review"


@pytest.mark.skipif(not IMPORTS_OK, reason="router imports require research_agent")
class TestToolRouting:

    def setup_method(self):
        register_builtins()

    def test_routing_reduces_tools(self):
        """Intent routing should expose fewer tools than full registry for narrow intents."""
        registry = get_registry()
        full_count = len(registry.list_for_llm())

        # Narrow intents should filter significantly
        narrow_intents = ["search", "git", "execute", "read"]
        for intent in narrow_intents:
            filtered = route_tools(intent, registry)
            assert len(filtered) < full_count, f"intent '{intent}' has {len(filtered)}/{full_count} tools"

    def test_git_intent_only_git_tools(self):
        """Git intent exposes git tools."""
        registry = get_registry()
        filtered = route_tools("git", registry)
        tool_names = [t["function"]["name"] for t in filtered]
        # All returned tools should be git_ prefixed
        git_names = [n for n in tool_names if n.startswith("git_")]
        assert len(git_names) >= 4  # at least 4 git tools
        assert len(git_names) == len(tool_names)  # and ONLY git tools

    def test_default_exposes_all(self):
        """Default intent should expose most tools."""
        registry = get_registry()
        filtered = route_tools("default", registry)
        full_count = len(registry.list_for_llm())
        assert len(filtered) >= full_count - 1  # default includes all + subagent

    def test_all_intents_work(self):
        """Every intent label produces a non-empty tool list."""
        registry = get_registry()
        for intent in INTENT_ROUTES:
            filtered = route_tools(intent, registry)
            assert len(filtered) > 0, f"intent '{intent}' returned 0 tools"


class TestAntiConfusionHints:
    """Check that disambiguation hints are provided for confusable pairs."""

    def test_all_key_pairs_have_hints(self):
        """Every pair known to be confusable has a hint."""
        base_descs = {
            "retrieve": "search local DB",
            "search_papers": "search arXiv",
            "read_paper": "read paper in DB",
            "file_read": "read file from disk",
            "file_write": "write file to disk",
            "file_edit": "edit file content",
            "file_glob": "find files by name",
            "file_grep": "search file content",
            "shell_exec": "run command",
            "check_tasks": "check background tasks",
        }
        enhanced = add_anti_confusion_hints(base_descs)
        for name, desc in enhanced.items():
            assert "使用说明" in desc or name not in [
                "retrieve", "search_papers", "read_paper", "file_read",
                "file_write", "file_edit", "file_glob", "file_grep",
                "shell_exec", "check_tasks",
            ]

    def test_hint_distinguishes_pair(self):
        """Each hint should mention the alternative tool."""
        descs = {"retrieve": "a", "search_papers": "b"}
        enhanced = add_anti_confusion_hints(descs)
        assert "search_papers" in enhanced["retrieve"] or "search" in enhanced["retrieve"].lower()


class TestSimilarityScores:

    def test_identical_descriptions_high_score(self):
        """Identical descriptions should have high similarity."""
        scores = compute_similarity_scores({"a": "search database", "b": "search database"})
        assert scores[0]["similarity"] > 0.9

    def test_different_descriptions_low_score(self):
        """Very different descriptions should have low similarity."""
        scores = compute_similarity_scores({"a": "search database", "b": "run shell command"})
        if scores:
            assert scores[0]["similarity"] < 0.5

    def test_scores_are_sorted_descending(self):
        """Similarity scores should be sorted from highest to lowest."""
        scores = compute_similarity_scores({
            "a": "search papers",
            "b": "find articles",
            "c": "run command",
        })
        for i in range(len(scores) - 1):
            assert scores[i]["similarity"] >= scores[i + 1]["similarity"]


@pytest.mark.skipif(not IMPORTS_OK, reason="router imports require research_agent")
class TestAuditReport:
    """Full audit produces a structured report."""

    def test_audit_produces_report(self):
        register_builtins()
        registry = get_registry()
        report = audit_tool_confusion(registry)
        assert "total_tools" in report
        assert "warnings" in report
        assert "critical_pairs" in report
        assert report["total_tools"] >= 17


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
