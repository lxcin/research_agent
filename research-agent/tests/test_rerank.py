# tests/test_rerank.py — MMR diversity re-ranking + tier_b integration
import pytest

from research_agent.memory.rerank import mmr_select
from research_agent.memory import storage, vector as vec_mod
from research_agent.memory.models import MemoryUnit, MemoryKind, MemoryScope


def test_mmr_relevance_only_keeps_topk():
    ids = ["a", "b", "c"]
    vecs = [[1, 0], [1, 0], [0.8, 0.6]]  # a,b identical & most relevant
    out = mmr_select([1, 0], vecs, ids, k=2, lam=1.0)
    assert out == ["a", "b"]  # pure relevance


def test_mmr_diversity_prefers_dissimilar():
    ids = ["a", "b", "c"]
    vecs = [[1, 0], [1, 0], [0.8, 0.6]]  # a,b duplicates; c diverse
    out = mmr_select([1, 0], vecs, ids, k=2, lam=0.3)
    assert out[0] == "a"
    assert out[1] == "c"  # picks the diverse one instead of duplicate b


def test_mmr_empty_and_k_larger_than_n():
    assert mmr_select([1, 0], [], [], k=3) == []
    ids = ["a", "b"]
    out = mmr_select([1, 0], [[1, 0], [0, 1]], ids, k=5, lam=0.5)
    assert set(out) == {"a", "b"} and len(out) == 2


def test_mmr_respects_k_limit():
    ids = [f"u{i}" for i in range(6)]
    vecs = [[1, 0.1 * i] for i in range(6)]
    out = mmr_select([1, 0], vecs, ids, k=2, lam=0.7)
    assert len(out) == 2


# ── tier_b integration: vector-first + MMR, keyword fallback ────────────────

def _seed(units):
    for uid, text, kind in units:
        storage.upsert(MemoryUnit(id=uid, text=text, kind=kind,
                                  importance=0.5, scope=MemoryScope.USER))


def test_retrieve_falls_back_to_keyword_when_vector_unavailable(temp_data_dir, monkeypatch):
    monkeypatch.setattr(vec_mod, "is_available", lambda: False)
    _seed([("m1", "用户偏好用中文写邮件并签名", MemoryKind.PREFERENCE)])
    from research_agent.memory.tier_b import get_manager
    hits = get_manager().retrieve("中文邮件签名")
    assert hits and hits[0].id == "m1"


def test_retrieve_vector_mmr_path(temp_data_dir, monkeypatch):
    monkeypatch.setattr(vec_mod, "is_available", lambda: True)
    monkeypatch.setattr(vec_mod, "encode_query", lambda q: [1.0, 0.0])
    # query vector [1,0]; m1,m2 near-duplicate; m3 diverse
    monkeypatch.setattr(vec_mod, "query_with_embeddings",
                        lambda q, n_results=20: [
                            {"id": "m1", "distance": 0.0, "embedding": [1.0, 0.0]},
                            {"id": "m2", "distance": 0.0, "embedding": [1.0, 0.0]},
                            {"id": "m3", "distance": 0.5, "embedding": [0.8, 0.6]},
                        ])
    _seed([("m1", "事实一", MemoryKind.FACT),
           ("m2", "事实一重复", MemoryKind.FACT),
           ("m3", "另一角度", MemoryKind.FACT)])
    import research_agent.memory.tier_b as tb
    monkeypatch.setattr(tb, "MMR_LAMBDA", 0.3)  # favor diversity for this unit test
    hits = tb.get_manager().retrieve("query", limit=2)
    ids = [u.id for u in hits]
    assert ids[0] == "m1"
    assert "m3" in ids  # diverse pick over near-duplicate m2


def test_retrieve_mmr_lambda_default_present(temp_data_dir):
    import research_agent.memory.tier_b as tb
    assert 0.0 < tb.MMR_LAMBDA <= 1.0


def test_retrieve_vector_path_filters_scope(temp_data_dir, monkeypatch):
    monkeypatch.setattr(vec_mod, "is_available", lambda: True)
    monkeypatch.setattr(vec_mod, "encode_query", lambda q: [1.0, 0.0])
    monkeypatch.setattr(vec_mod, "query_with_embeddings",
                        lambda q, n_results=20: [
                            {"id": "u1", "distance": 0.0, "embedding": [1.0, 0.0]},
                            {"id": "p1", "distance": 0.0, "embedding": [1.0, 0.0]},
                        ])
    storage.upsert(MemoryUnit(id="u1", text="user fact", kind=MemoryKind.FACT,
                              importance=0.5, scope=MemoryScope.USER))
    storage.upsert(MemoryUnit(id="p1", text="project fact", kind=MemoryKind.FACT,
                              importance=0.5, scope=MemoryScope.PROJECT))
    from research_agent.memory.tier_b import get_manager
    hits = get_manager().retrieve("q", scope=MemoryScope.USER, limit=5)
    assert [h.id for h in hits] == ["u1"]
