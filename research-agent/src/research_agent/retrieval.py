"""Hybrid retrieval: vector search + BM25 keyword search + RRF fusion."""
from functools import lru_cache

from rank_bm25 import BM25Okapi

from research_agent.vector_store import get_collection


@lru_cache(maxsize=1)
def _get_bm25() -> tuple[BM25Okapi, list[dict]]:
    coll = get_collection()
    chunks = coll.get()
    if not chunks["documents"]:
        return BM25Okapi([]), []
    tokenized = [doc.split() for doc in chunks["documents"]]
    bm25 = BM25Okapi(tokenized)
    all_data = [
        {"text": chunks["documents"][i],
         "paper_id": chunks["metadatas"][i].get("paper_id", ""),
         "chunk_index": chunks["metadatas"][i].get("chunk_index", 0)}
        for i in range(len(chunks["documents"]))
    ]
    return bm25, all_data


def build_bm25_index():
    """Force rebuild BM25 index (call after adding/deleting chunks)."""
    _get_bm25.cache_clear()
    _get_bm25()


def _reciprocal_rank_fusion(vector_results: list[dict], bm25_results: list[dict], k: int = 60) -> list[dict]:
    scores: dict[str, float] = {}
    docs: dict[str, dict] = {}

    for rank, doc in enumerate(vector_results):
        key = doc["paper_id"] + "_" + str(doc["chunk_index"])
        scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)
        docs[key] = doc

    for rank, doc in enumerate(bm25_results):
        key = doc["paper_id"] + "_" + str(doc["chunk_index"])
        scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)
        docs[key] = doc

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [docs[key] | {"score": score} for key, score in ranked]


def _vector_search(query: str, n_results: int) -> list[dict]:
    coll = get_collection()
    results = coll.query(query_texts=[query], n_results=n_results)
    if not results["ids"] or not results["ids"][0]:
        return []
    return [
        {"paper_id": results["metadatas"][0][i].get("paper_id", ""),
         "chunk_index": results["metadatas"][0][i].get("chunk_index", 0),
         "text": results["documents"][0][i],
         "score": 1.0 - results["distances"][0][i] if results["distances"] else 0.0}
        for i in range(len(results["ids"][0]))
    ]


def _bm25_search(query: str, n_results: int) -> list[dict]:
    bm25, all_data = _get_bm25()
    if not all_data:
        return []
    tokenized_query = query.split()
    scores = bm25.get_scores(tokenized_query)
    indexed_scores = list(enumerate(scores))
    indexed_scores.sort(key=lambda x: x[1], reverse=True)
    top_n = indexed_scores[:n_results]
    max_score = max(scores) if max(scores) > 0 else 1
    return [
        all_data[i] | {"score": score / max_score}
        for i, score in top_n
    ]


def hybrid_search(query: str, n_results: int = 5) -> list[dict]:
    vector_results = _vector_search(query, n_results * 2)
    bm25_results = _bm25_search(query, n_results * 2)
    fused = _reciprocal_rank_fusion(vector_results, bm25_results)
    return fused[:n_results]