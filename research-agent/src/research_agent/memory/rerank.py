"""MMR (Maximal Marginal Relevance) re-ranking for memory retrieval.

Dense top-k often returns near-duplicate memories (e.g. several paraphrases of
the same preference), hurting recall of *other* relevant units — especially for
multi-gold / aggregate queries. MMR greedily selects candidates that are both
relevant to the query and dissimilar to what's already picked:

    MMR = argmax_{d in C\\S} [ λ·sim(q,d) − (1−λ)·max_{s in S} sim(d,s) ]

Model-free and deterministic: operates on already-normalized embeddings, so it's
unit-testable without Chroma or a model.
"""
from __future__ import annotations
from typing import Sequence


def mmr_select(query_vec, candidate_vecs, candidate_ids: Sequence[str],
               k: int, lam: float = 0.5) -> list[str]:
    """Return up to k candidate ids selected by MMR.

    query_vec: 1-D array-like (embedding of the query, normalized)
    candidate_vecs: 2-D array-like (n × dim), each row normalized
    candidate_ids: ids aligned with candidate_vecs
    lam: 0=fully diverse, 1=fully relevance (top-k)
    """
    import numpy as np
    q = np.asarray(query_vec, dtype=float)
    if len(candidate_ids) == 0:
        return []
    C = np.asarray(candidate_vecs, dtype=float)
    if C.ndim == 1:
        C = C.reshape(1, -1)
    n = C.shape[0]
    k = max(0, min(k, n))

    # relevance to query
    rel = C @ q
    # pairwise similarity between candidates
    sim = C @ C.T
    np.fill_diagonal(sim, -np.inf)  # never penalize an item against itself

    selected: list[int] = []
    remaining = list(range(n))
    for _ in range(k):
        best_i = None
        best_score = -np.inf
        for i in remaining:
            if selected:
                redundancy = max(sim[i, j] for j in selected)
            else:
                redundancy = 0.0
            score = lam * rel[i] - (1.0 - lam) * redundancy
            if score > best_score:
                best_score = score
                best_i = i
        if best_i is None:
            break
        selected.append(best_i)
        remaining.remove(best_i)

    return [candidate_ids[i] for i in selected]
