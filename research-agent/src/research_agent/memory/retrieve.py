"""Tier B read path: ROUTE → RETRIEVE → RANK/TRIM → text block for injection.

ROUTE decides whether the request needs personal/global memory at all (so we do
not pollute every request with memory lookups). RETRIEVE runs on USER scope by
default (cross-project personal memory); project working memory already lives
in progress.md (Tier A). The output is a small, self-contained text block that
context.py injects at a fixed position, each entry carrying a source trace.
"""
import re

from research_agent.memory import get_manager
from research_agent.memory.models import MemoryScope, MemoryUnit

# Deterministic route triggers — personal-memory style questions/statements.
# Deliberately narrow: general research queries should NOT hit global memory.
_MEMORY_TRIGGERS = [
    "我记得", "记不记得", "你还记得", "我说过", "我之前说过", "我告诉过你",
    "你之前说过", "你偏好", "我的偏好", "我喜欢", "我的习惯", "我的风格",
    "我的领域", "我的方向", "我是谁", "记住", "remember", "i told you",
    "my preference", "what do i like", "who am i", "my field", "my style",
    "上次你说", "你了解我吗", "了解我",
]

_TRIGGER_RE = re.compile("|".join(re.escape(t) for t in _MEMORY_TRIGGERS), re.IGNORECASE)


def route(user_input: str) -> bool:
    """Deterministic gate: should we consult Tier B memory for this input?"""
    return bool(user_input and _TRIGGER_RE.search(user_input))


def route_llm(llm, user_input: str) -> bool:
    """Optional small-model route (fallback / richer gate). Not used by default."""
    prompt = (
        "判断以下用户输入是否需要查询'关于用户的长期记忆'（涉及用户本人偏好、说过的事、"
        "领域、风格、历史决定）。只需要答 yes/no。\n"
        f"输入: {user_input}\n输出: "
    )
    try:
        raw = llm.complete([{"role": "user", "content": prompt}], max_tokens=5)
        return raw.strip().lower().startswith("yes")
    except Exception:
        return route(user_input)


def _query_from_input(user_input: str) -> str:
    """Use the input as the query; strip obvious personal-memory shell phrases."""
    q = user_input
    q = re.sub(r"(你)?(还记得|记不记得|我之前|我说过|我告诉过你|上次你说过)?[:：]?", "", q, flags=re.IGNORECASE)
    return q.strip() or user_input


def retrieve(user_input: str, scope: MemoryScope = MemoryScope.USER,
             limit: int = 6, query: str | None = None) -> list[MemoryUnit]:
    """C.2 RETRIEVE: ranked units from USER (cross-project) memory."""
    q = query or _query_from_input(user_input)
    if not q:
        return []
    mgr = get_manager()
    return mgr.retrieve(q, scope=scope, limit=limit)


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


def format_block(units: list[MemoryUnit], max_tokens: int = 1500,
                 count_tokens=None) -> str:
    """C.3 RANK/TRIM: build injectable <Global Memory> text within budget."""
    if not units:
        return ""
    if count_tokens is None:
        try:
            from research_agent.context import count_tokens as _ct
            count_tokens = _ct
        except Exception:
            count_tokens = lambda s: len(s) // 4

    lines: list[str] = []
    used = 0
    header_tokens = count_tokens("<Global Memory>") + 20  # allow wrappers
    for u in units:
        kind_label = u.kind.value if hasattr(u.kind, "value") else str(u.kind)
        entry = f"- [{kind_label}] {u.text}{_source_trace(u)}"
        cost = count_tokens(entry) + 2
        if used + cost + header_tokens > max_tokens:
            break
        lines.append(entry)
        used += cost
    if not lines:
        return ""
    return "<Global Memory/全局记忆(你说过/做过)>\n" + "\n".join(lines) + "\n</Global Memory>"


def build_memory_block(user_input: str, max_tokens: int = 1500) -> str:
    """ROUTE → RETRIEVE → TRIM, returns block string ('' when not triggered)."""
    if not route(user_input):
        return ""
    units = retrieve(user_input)
    return format_block(units, max_tokens=max_tokens)
