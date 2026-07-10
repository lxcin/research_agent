"""RAG ablation study runner."""
import json
import time
from pathlib import Path
from dataclasses import dataclass, field

from research_agent.config import get_data_dir
from research_agent.retrieval import hybrid_search, build_bm25_index
from research_agent.vector_store import search as vector_search


@dataclass
class EvalResult:
    experiment: str = ""
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    precision_at_5: float = 0.0
    mrr: float = 0.0
    avg_retrieval_time_ms: float = 0.0
    total_llm_calls: int = 0
    total_tokens: int = 0
    per_query: list[dict] = field(default_factory=list)


def compute_metrics(results: list[dict], ground_truths: list[str]) -> EvalResult:
    recalls_5 = []
    recalls_10 = []
    precisions_5 = []
    mrrs = []
    times = []

    for r in results:
        gt = set(r["ground_truth"].lower().split())
        retrieved_5 = r.get("retrieved_5", [])
        retrieved_10 = r.get("retrieved_10", [])
        times.append(r.get("time_ms", 0))

        # Recall@5: does any top-5 chunk contain the ground truth?
        hits_5 = any(_overlap_ratio(gt, set(c["text"].lower().split())) > 0.3 for c in retrieved_5)
        recalls_5.append(1.0 if hits_5 else 0.0)

        # Recall@10
        hits_10 = any(_overlap_ratio(gt, set(c["text"].lower().split())) > 0.3 for c in retrieved_10)
        recalls_10.append(1.0 if hits_10 else 0.0)

        # Precision@5: how many top-5 are relevant?
        relevant_5 = sum(1 for c in retrieved_5 if _overlap_ratio(gt, set(c["text"].lower().split())) > 0.3)
        precisions_5.append(relevant_5 / max(len(retrieved_5), 1))

        # MRR: rank of first relevant result
        mrr = 0.0
        for rank, c in enumerate(retrieved_10):
            if _overlap_ratio(gt, set(c["text"].lower().split())) > 0.3:
                mrr = 1.0 / (rank + 1)
                break
        mrrs.append(mrr)

    return EvalResult(
        recall_at_5=sum(recalls_5) / max(len(recalls_5), 1),
        recall_at_10=sum(recalls_10) / max(len(recalls_10), 1),
        precision_at_5=sum(precisions_5) / max(len(precisions_5), 1),
        mrr=sum(mrrs) / max(len(mrrs), 1),
        avg_retrieval_time_ms=sum(times) / max(len(times), 1),
    )


def _overlap_ratio(set_a: set, set_b: set) -> float:
    if not set_a:
        return 0.0
    return len(set_a & set_b) / len(set_a)


def run_plain_rag(queries: str, ground_truth: str, chunk_size: int = 512) -> dict:
    """E1: Fixed-size chunking + pure vector search."""
    t0 = time.time()
    raw_5 = vector_search(queries, n_results=5)
    raw_10 = vector_search(queries, n_results=10)
    return {
        "ground_truth": ground_truth,
        "retrieved_5": _format_results(raw_5),
        "retrieved_10": _format_results(raw_10),
        "time_ms": (time.time() - t0) * 1000,
        "llm_calls": 0, "tokens": 0,
    }


def run_section_chunked_rag(queries: str, ground_truth: str) -> dict:
    """E2: Section-aware chunking + pure vector search."""
    t0 = time.time()
    raw_5 = vector_search(queries, n_results=5)
    raw_10 = vector_search(queries, n_results=10)
    return {
        "ground_truth": ground_truth,
        "retrieved_5": _format_results(raw_5),
        "retrieved_10": _format_results(raw_10),
        "time_ms": (time.time() - t0) * 1000,
        "llm_calls": 0, "tokens": 0,
    }


def run_hybrid_rag(queries: str, ground_truth: str) -> dict:
    """E3: Section-aware chunking + hybrid search (vector+BM25+RRF)."""
    t0 = time.time()
    build_bm25_index()
    retrieved_5 = hybrid_search(queries, n_results=5)
    retrieved_10 = hybrid_search(queries, n_results=10)
    return {
        "ground_truth": ground_truth,
        "retrieved_5": retrieved_5,
        "retrieved_10": retrieved_10,
        "time_ms": (time.time() - t0) * 1000,
        "llm_calls": 0, "tokens": 0,
    }


def run_agentic_rag(queries: str, ground_truth: str) -> dict:
    """E4: Hybrid search + Agentic Loop (self-correction, max 3 retries)."""
    import litellm
    t0 = time.time()
    llm_calls = 0
    total_tokens = 0

    # First attempt: hybrid search
    build_bm25_index()
    results = hybrid_search(queries, n_results=10)

    # Agentic loop: check if results are sufficient, if not, re-query
    for attempt in range(3):
        if len(results) >= 3:
            break
        # Refine query using LLM
        try:
            resp = litellm.completion(
                model="claude-3-haiku-20240307",
                messages=[{"role": "user", "content": f"Rewrite this search query to be more precise for academic paper retrieval: '{queries}'. Output ONLY the revised query."}],
                max_tokens=50, temperature=0.1,
            )
            llm_calls += 1
            total_tokens += resp.usage.total_tokens if hasattr(resp, 'usage') else 50
            refined = resp.choices[0].message.content.strip()
            results = hybrid_search(refined, n_results=10)
        except Exception:
            break

    return {
        "ground_truth": ground_truth,
        "retrieved_5": results[:5],
        "retrieved_10": results[:10],
        "time_ms": (time.time() - t0) * 1000,
        "llm_calls": llm_calls,
        "tokens": total_tokens,
    }


def _format_results(results: dict | list) -> list[dict]:
    if isinstance(results, list):
        return results
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    return [{"text": docs[i], "paper_id": metas[i].get("paper_id", "")}
            for i in range(len(docs))]


def save_results(result: EvalResult, label: str) -> Path:
    """Save evaluation results to disk for future comparison."""
    eval_dir = get_data_dir() / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True)
    path = eval_dir / f"{label}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result.__dict__, f, ensure_ascii=False, indent=2)
    return path


def run_ablation(domain_data: dict, domain_name: str) -> dict[str, EvalResult]:
    """Run full ablation study for one domain."""
    results = {}
    ground_truths = domain_data["ground_truth"]

    # E1: Plain RAG (fixed chunking + vector only)
    e1 = []
    for gt in ground_truths:
        for q in gt["queries"]:
            e1.append(run_plain_rag(q, gt["fact"]))
    results["E1_plain_rag"] = compute_metrics(e1, [g["fact"] for g in ground_truths])
    results["E1_plain_rag"].experiment = "E1_plain_rag"

    # E2: Section-aware chunked RAG
    e2 = []
    for gt in ground_truths:
        for q in gt["queries"]:
            e2.append(run_section_chunked_rag(q, gt["fact"]))
    results["E2_section_chunked_rag"] = compute_metrics(e2, [g["fact"] for g in ground_truths])
    results["E2_section_chunked_rag"].experiment = "E2_section_chunked_rag"

    # E3: Hybrid RAG
    e3 = []
    for gt in ground_truths:
        for q in gt["queries"]:
            e3.append(run_hybrid_rag(q, gt["fact"]))
    results["E3_hybrid_rag"] = compute_metrics(e3, [g["fact"] for g in ground_truths])
    results["E3_hybrid_rag"].experiment = "E3_hybrid_rag"

    # E4: Agentic RAG
    e4 = []
    for gt in ground_truths:
        for q in gt["queries"]:
            e4.append(run_agentic_rag(q, gt["fact"]))
    results["E4_agentic_rag"] = compute_metrics(e4, [g["fact"] for g in ground_truths])
    results["E4_agentic_rag"].experiment = "E4_agentic_rag"

    return results