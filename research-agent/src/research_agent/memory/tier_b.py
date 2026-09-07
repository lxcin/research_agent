"""Tier B personal memory — facade over SQLite + optional vector layer.

Hosts the MemoryManager and re-exports the models/enums so feature modules
(source/extractor/pipeline/retrieve/memory_tool) and tests import from here.
Kept separate from research_agent.memory.__init__ (core conversation API) so the
whole Tier B subsystem can be disabled/uninstalled without touching core.
"""
from research_agent.memory.models import MemoryUnit, MemoryScope, MemoryKind
from research_agent.memory import storage, vector

RRF_K = 60


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
        keyword_hits = storage.search_keyword(query, scope=scope, kind=kind, limit=limit * 3)
        vector_hits: list[dict] = vector.query(query, n_results=limit * 3) if vector.is_available() else []

        if not vector_hits:
            return keyword_hits[:limit]

        scores: dict[str, float] = {}
        order: dict[str, MemoryUnit] = {}
        for rank, u in enumerate(keyword_hits):
            scores[u.id] = scores.get(u.id, 0.0) + 1.0 / (RRF_K + rank + 1)
            order[u.id] = u
        for rank, hit in enumerate(vector_hits):
            uid = hit["id"]
            scores[uid] = scores.get(uid, 0.0) + 1.0 / (RRF_K + rank + 1)
            if uid not in order:
                u = storage.get(uid)
                if u:
                    order[uid] = u
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return [order[uid] for uid, _ in ranked[:limit] if uid in order]


# Singleton
_manager: MemoryManager | None = None


def get_manager() -> MemoryManager:
    global _manager
    if _manager is None:
        _manager = MemoryManager()
    return _manager
