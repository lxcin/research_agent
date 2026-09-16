"""Tier B personal memory — facade over SQLite + optional vector layer.

Hosts the MemoryManager and re-exports the models/enums so feature modules
(source/extractor/pipeline/retrieve/memory_tool) and tests import from here.
Kept separate from research_agent.memory.__init__ (core conversation API) so the
whole Tier B subsystem can be disabled/uninstalled without touching core.
"""
from research_agent.memory.models import MemoryUnit, MemoryScope, MemoryKind
from research_agent.memory import storage, vector

# MMR relevance/diversity trade-off (0=pure diversity, 1=pure relevance).
# 0.7 was best in tests/eval_memory_agentic.py (R@5 89.8%->91.6%, aggregate
# R@5 45%->55%). Overridable for tuning/tests.
import os as _os
MMR_LAMBDA = float(_os.environ.get("RESEARCH_AGENT_MMR_LAMBDA", "0.7"))


def reset_for_tests():
    """Close SQLite + drop vector cache + force vector recheck (test hook)."""
    storage.reset_db_for_tests()
    vector.set_available(None)
    import research_agent.memory.vector as v
    v._coll = None


class MemoryManager:
    """Read/write facade over Tier B memory (SQLite + optional vector)."""

    def write(self, unit: MemoryUnit) -> MemoryUnit:
        """Persist one unit (dedupe/conflict handling lives in extractor)."""
        unit = storage.upsert(unit)
        if vector.is_available():
            cid = vector.add(unit.id, unit.text,
                             {"scope": unit.scope.value, "kind": unit.kind.value})
            if cid and cid != unit.embedding_id:
                unit.embedding_id = cid
                storage.upsert(unit)
        return unit

    def update(self, unit: MemoryUnit) -> MemoryUnit:
        return storage.upsert(unit)

    def supersede(self, old_id: str, new_unit: MemoryUnit) -> MemoryUnit | None:
        """Mark old inactive + persist replacement."""
        saved = storage.supersede(old_id, new_unit)
        if saved and vector.is_available():
            vector.remove(old_id)
            vector.add(saved.id, saved.text,
                       {"scope": saved.scope.value, "kind": saved.kind.value})
        return saved

    def delete(self, unit_id: str) -> bool:
        ok = storage.delete(unit_id)
        if ok:
            vector.remove(unit_id)
        return ok

    def get(self, unit_id: str) -> MemoryUnit | None:
        return storage.get(unit_id)

    def list_units(self, scope: MemoryScope | None = None, kind: MemoryKind | None = None,
                   active_only: bool = True, limit: int = 100) -> list[MemoryUnit]:
        return storage.list_units(scope=scope, kind=kind,
                                  active_only=active_only, limit=limit)

    def count(self) -> int:
        return storage.count()

    # ── Retrieval: RRF fusion of keyword (always on) + vector (when available)

    def retrieve(self, query: str, scope: MemoryScope | None = None,
                 kind: MemoryKind | None = None, limit: int = 5) -> list[MemoryUnit]:
        """Retrieve ranked units.

        Strategy is data-driven (see tests/eval_memory_*.py):
          1) vector-first when embeddings are available, re-ranked by MMR
             (λ=0.7) to avoid returning near-duplicate memories;
          2) keyword search as fallback (or when vector layer is degraded).
        Equal-weight keyword+vector RRF was measured to be WORSE than plain
        vector here (keyword noise), so keyword is not fused in the vector path.
        """
        if vector.is_available():
            try:
                hits = vector.query_with_embeddings(query, n_results=max(limit * 4, 20))
            except Exception:
                hits = []
            picked = self._mmr_pick(query, hits, scope, kind, limit)
            if picked:
                return picked
        return storage.search_keyword(query, scope=scope, kind=kind, limit=limit)[:limit]

    def _mmr_pick(self, query, hits, scope, kind, limit):
        """Filter vector candidates by scope/kind, then MMR-select top-limit."""
        from research_agent.memory.rerank import mmr_select
        filt = []
        for h in hits:
            u = storage.get(h["id"])
            if not u or not u.active:
                continue
            if scope is not None and u.scope != scope:
                continue
            if kind is not None and u.kind != kind:
                continue
            filt.append((h, u))
        if not filt:
            return []
        qv = vector.encode_query(query)
        have_emb = all(h.get("embedding") is not None for h, _ in filt)
        if qv is not None and have_emb and len(filt) > 1:
            embs = [h["embedding"] for h, _ in filt]
            cids = [h["id"] for h, _ in filt]
            order = {uid: u for uid, u in ((h["id"], u) for h, u in filt)}
            selected = mmr_select(qv, embs, cids, limit, lam=MMR_LAMBDA)
            return [order[i] for i in selected if i in order]
        return [u for _, u in filt[:limit]]


# Singleton
_manager: MemoryManager | None = None


def get_manager() -> MemoryManager:
    global _manager
    if _manager is None:
        _manager = MemoryManager()
    return _manager
