"""Tier B read path — helpers for the search_memory tool (agentic read).

Retrieval is no longer auto-injected into context. Instead the LLM decides when
personal memory matters and calls the `search_memory` tool, which runs keyword
retrieval (scope/kind filtered) on the USER-scope memory store and returns
ranked MemoryUnits with source traces. This module hosts the retrieval + a
readable formatter used by that tool.
"""
from research_agent.memory.tier_b import get_manager
from research_agent.memory.models import MemoryScope, MemoryUnit


def retrieve(query: str, scope: MemoryScope = MemoryScope.USER,
             limit: int = 6) -> list[MemoryUnit]:
    """RETRIEVE: ranked units from USER (cross-project) memory by keyword query."""
    if not query or not query.strip():
        return []
    return get_manager().retrieve(query, scope=scope, limit=limit)


def _source_trace(unit: MemoryUnit) -> str:
    src = unit.source or {}
    bits = []
    if src.get("project_id"):
        bits.append(f"project:{src['project_id'][:8]}")
    if src.get("chat_id"):
        bits.append(f"chat:{src['chat_id'][-8:]}")
    if not bits and unit.created_at:
        bits.append(unit.created_at[:10])
    return f" ({', '.join(bits)})" if bits else ""


def format_hits(units: list[MemoryUnit]) -> str:
    """Human/LLM-readable list of memory hits (with kind + source trace)."""
    if not units:
        return ""
    lines = [f"- [{u.kind.value}] {u.text}{_source_trace(u)}" for u in units]
    return "长期记忆命中（用户曾说过/做过）:\n" + "\n".join(lines)
