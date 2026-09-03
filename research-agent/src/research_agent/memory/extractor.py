"""EXTRACT + VERIFY for Tier B memory write path.

EXTRACT: small model reads a conversation-only source and distills durable,
tool-free MemoryUnits (kind + importance + self-contained text).
VERIFY: dedupe & conflict detection against existing units using a cheap
SequenceMatcher pre-filter then a small-model verdict when near-identical
(duplicate / opposite → keep both / unrelated).

All prompts are pure; every branch is testable with MockLLMProvider.
"""
import json
import re
from difflib import SequenceMatcher

from research_agent.memory.models import MemoryUnit, MemoryKind, MemoryScope
from research_agent.memory import storage

KIND_HINTS = {
    MemoryKind.FACT: "关于用户的持久事实（身份、领域、技能、习惯）",
    MemoryKind.PREFERENCE: "用户偏好（语言、工具、风格、流程）",
    MemoryKind.DECISION: "用户做过的决定（含原因）",
    MemoryKind.TASK: "用户承诺要做的事 / 待办 / 计划",
    MemoryKind.DEAD_END: "验证过不可行的方向、坑，避免重复",
    MemoryKind.INSIGHT: "跨对话/跨项目的综合认识",
    MemoryKind.REFERENCE: "用户提到的外部对象/术语/链接",
    MemoryKind.STYLE: "写作/工具使用风格要求",
}

_ALLOWED_KINDS = {k.value for k in MemoryKind}

EXTRACT_PROMPT = """你是个人助手的记忆提炼器。从对话中提取"关于这个用户"的持久信息。

规则：
1. 只提取持久信息：用户的领域、偏好、决定、说过要做的任务、踩过的坑、风格要求。
2. 不要提取临时内容：检索到的论文/网页内容、工具执行细节、代码输出、命令。
3. 一句话一条（text），自包含、可独立检索；不要用代词，写成"用户…"。
4. kind 只能从这些里选：{kinds}。
5. importance 0~1：决定/强烈偏好/经常提到给 0.8+；一般事实 0.5；模糊提及 0.2。
6. 如果这段对话没有任何值得长期记住的信息，输出空数组 []。

只输出 JSON 数组，不要 markdown fence：
[{{"kind": "preference", "text": "用户偏好…", "importance": 0.7}}]

对话：
{conversation}"""


def _strip_fences(raw: str) -> str:
    return re.sub(r"^```(?:json)?\s*", "", raw.strip()).rstrip("`").strip()


def _parse_extract(raw: str) -> list[dict]:
    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return data


def extract_units(llm, conversation: str,
                  scope: MemoryScope = MemoryScope.USER,
                  source: dict | None = None) -> list[MemoryUnit]:
    """Run the small-model EXTRACT step. Never raises on parse failure."""
    if not conversation or not conversation.strip():
        return []
    kinds = ", ".join(_ALLOWED_KINDS)
    prompt = EXTRACT_PROMPT.format(kinds=kinds, conversation=conversation[:6000])
    try:
        raw = llm.complete([{"role": "user", "content": prompt}], max_tokens=600)
    except Exception:
        return []
    units = []
    for item in _parse_extract(raw):
        text = (item.get("text") or "").strip()
        kind_raw = (item.get("kind") or "").strip().lower()
        if not text:
            continue
        if kind_raw not in _ALLOWED_KINDS:
            continue
        try:
            importance = max(0.0, min(1.0, float(item.get("importance", 0.5))))
        except (TypeError, ValueError):
            importance = 0.5
        units.append(MemoryUnit(
            text=text,
            kind=MemoryKind(kind_raw),
            importance=importance,
            scope=scope,
            source=source or {},
        ))
    return units


# ── VERIFY ──────────────────────────────────────────────────────────────────

def dedupe_threshold(a: str, b: str, threshold: float = 0.82) -> bool:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() > threshold


def verify_new_units(llm, new_units: list[MemoryUnit], scope: MemoryScope,
                     threshold: float = 0.82) -> list[MemoryUnit]:
    """Drop units too similar to existing ones; keep 'opposite' pairs both.

    Deterministic pre-filter by string similarity, LLM only for the ambiguous
    band (0.82–0.95). Pure duplicates (>0.95) are always dropped without LLM.
    """
    existing = storage.list_units(scope=scope, active_only=True, limit=300)
    if not existing or not new_units:
        return new_units

    keep: list[MemoryUnit] = []
    for nu in new_units:
        duplicate = False
        for eu in existing:
            ratio = SequenceMatcher(None, nu.text.lower(), eu.text.lower()).ratio()
            if ratio > 0.95:
                duplicate = True
                break
            if 0.82 <= ratio <= 0.95:
                verdict = _llm_verdict(llm, eu.text, nu.text)
                if verdict == "duplicate":
                    duplicate = True
                    break
                # 'opposite' / 'unrelated' → keep both
        if not duplicate:
            keep.append(nu)
    return keep


def _llm_verdict(llm, a: str, b: str) -> str:
    prompt = (
        f"判断两个记忆条目是重复(duplicate)、相反观点(opposite)还是不相关(unrelated)。\n"
        f"A: {a}\nB: {b}\n"
        f"注意：同一概念但结论相反输出 opposite（两条都要保留）。\n"
        f"只输出一个词: duplicate/opposite/unrelated"
    )
    try:
        raw = llm.complete([{"role": "user", "content": prompt}], max_tokens=10)
    except Exception:
        return "unrelated"
    verdict = raw.strip().lower()
    return verdict if verdict in ("duplicate", "opposite", "unrelated") else "unrelated"


# ── End-to-end convenience ──────────────────────────────────────────────────

def distill(llm, conversation: str, scope: MemoryScope = MemoryScope.USER,
            source: dict | None = None) -> list[MemoryUnit]:
    """EXTRACT then VERIFY. Returns units ready for storage."""
    units = extract_units(llm, conversation, scope=scope, source=source)
    if not units:
        return []
    return verify_new_units(llm, units, scope)
