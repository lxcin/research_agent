# tests/test_eval.py
from research_agent.eval.runner import compute_metrics, EvalResult, _overlap_ratio


def test_overlap_ratio():
    assert _overlap_ratio({"a", "b", "c"}, {"a", "b"}) == 2/3
    assert _overlap_ratio({"a"}, {"x", "y"}) == 0.0
    assert _overlap_ratio(set(), {"a"}) == 0.0


def test_compute_metrics_perfect():
    results = [
        {"ground_truth": "The Transformer uses scaled dot-product attention",
         "retrieved_5": [{"text": "The Transformer uses scaled dot-product attention"}, {"text": "other"}, {"text": "other"}, {"text": "other"}, {"text": "other"}],
         "retrieved_10": [{"text": "The Transformer uses scaled dot-product attention"}, {"text": "other"}, {"text": "other"}, {"text": "other"}, {"text": "other"}, {"text": "other"}, {"text": "other"}, {"text": "other"}, {"text": "other"}, {"text": "other"}],
         "time_ms": 10},
    ]
    result = compute_metrics(results, ["The Transformer uses scaled dot-product attention"])
    assert result.recall_at_5 == 1.0
    assert result.mrr == 1.0


def test_compute_metrics_miss():
    results = [
        {"ground_truth": "specific fact about transformers",
         "retrieved_5": [{"text": "unrelated content"}, {"text": "more unrelated"}, {"text": "other"}, {"text": "other"}, {"text": "other"}],
         "retrieved_10": [{"text": "unrelated"}, {"text": "more"}, {"text": "other"}, {"text": "other"}, {"text": "other"}, {"text": "other"}, {"text": "other"}, {"text": "other"}, {"text": "other"}, {"text": "other"}],
         "time_ms": 5},
    ]
    result = compute_metrics(results, ["specific fact about transformers"])
    assert result.recall_at_5 == 0.0
    assert result.mrr == 0.0


def test_benchmark_structure():
    from research_agent.eval.benchmark import BENCHMARK_DOMAINS
    for domain, data in BENCHMARK_DOMAINS.items():
        assert "seed_papers" in data
        assert "ground_truth" in data
        for gt in data["ground_truth"]:
            assert "fact" in gt
            assert "queries" in gt
            assert len(gt["queries"]) >= 2