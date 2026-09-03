"""Response validation: hallucination detection, citation verification, tool output validation."""
import json
import re
from dataclasses import dataclass, field
from research_agent.models import AgentState


# ── Response validator (original) ──

def validate_response(state: AgentState) -> AgentState:
    response = state.final_response or ""
    chunks = state.retrieved_context or []
    cited_ids = _extract_cited_paper_ids(response)
    valid_ids = {c.get("paper_id", "") for c in chunks if c.get("paper_id")}
    hallucinated = [cid for cid in cited_ids if cid not in valid_ids]

    if cited_ids and hallucinated:
        state.error = "hallucinated_citation: " + ", ".join(hallucinated)
        state.confidence = "uncertain"
    elif chunks and not cited_ids:
        state.confidence = "speculative"
        state.error = "retrieved_but_not_cited"
    elif not cited_ids and not chunks:
        state.confidence = "speculative"
        state.error = "no_retrieval_no_citation"
    else:
        state.confidence = "certain"
        state.citations = [f"paper:{cid}" for cid in cited_ids]
    return state


def _extract_cited_paper_ids(text: str) -> list[str]:
    ids = []
    for m in re.finditer(r"paper:([a-zA-Z0-9\-]{10,})", text):
        ids.append(m.group(1))
    for m in re.finditer(r"paper[_ ]?id[:\s]*([a-zA-Z0-9\-]{10,})", text, re.IGNORECASE):
        ids.append(m.group(1))
    return list(set(ids))


# ── Tool output validator (new, deterministic feedback) ──

@dataclass
class ValidationResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)


def validate_shell_output(data: dict) -> ValidationResult:
    if not data.get("success", True):
        stderr = data.get("stderr", "")
        rc = data.get("returncode", -1)
        return ValidationResult(passed=False, errors=[f"Command failed (rc={rc}): {stderr[:200]}"],
                                data={"retry_hint": "check stderr above"})
    return ValidationResult(passed=True, data={"output_length": len(data.get("stdout", ""))})


def validate_file_output(data: dict) -> ValidationResult:
    if not data.get("success", True):
        return ValidationResult(passed=False, errors=[f"File op failed: {data.get('error', 'unknown')}"])
    if data.get("size", 1) == 0:
        return ValidationResult(passed=True, warnings=["File written with 0 bytes"])
    return ValidationResult(passed=True, data={"size": data.get("size", 0)})


def validate_retrieval_result(data: dict) -> ValidationResult:
    found = data.get("found", 0)
    if found == 0:
        return ValidationResult(passed=True, warnings=["No results"], data={"hint": "try search_papers"})
    return ValidationResult(passed=True, data={"found": found})


def validate_result(tool_name: str, result_data: dict) -> ValidationResult:
    m = {"shell_exec": validate_shell_output, "file_write": validate_file_output,
         "file_edit": validate_file_output, "retrieve": validate_retrieval_result}
    v = m.get(tool_name)
    return v(result_data) if v else ValidationResult(passed=True)


# ── Semantic self-eval (Phase E, T3) ────────────────────────────────────────
# Deterministic rules + one cheap LLM call per run end (opt-in via
# RESEARCH_AGENT_SEMANTIC_CHECK=1). Detect: ungrounded citations, filler
# platitudes, reply contradicting tool results, answer-not-answering.

PLATITUDE_PATTERNS = [
    "我可以帮你", "我在这里帮你", "作为一个ai", "如果你有任何问题",
    "我会尽力", "if you have any questions", "happy to help",
]

FILLER_WORDS = {"好的", "明白了", "没问题", "了解", "嗯", "可以", "ok"}


def _read_paper_ids_from_messages(messages: list[dict]) -> set[str]:
    """Collect paper_ids actually read via read_paper tool calls this run."""
    ids: set[str] = set()
    for m in messages or []:
        if m.get("role") == "tool":
            content = m.get("content") or ""
            # tool result content for read_paper is a dict json with paper_id
            if '"paper_id"' in content:
                import json as _json
                try:
                    obj = _json.loads(content)
                    if isinstance(obj, dict) and obj.get("paper_id"):
                        ids.add(obj["paper_id"])
                except Exception:
                    continue
    return ids


def _extract_cited_ids_from_response(response: str) -> set[str]:
    ids = set()
    for m in re.finditer(r"paper:([a-zA-Z0-9_\-]{8,})", response):
        ids.add(m.group(1))
    for m in re.finditer(r"\[\s*(\d{1,3})\s*\]", response):
        ids.add(m.group(1))
    return ids


def rule_check_semantic(state, messages: list[dict] | None = None) -> list[dict]:
    """Deterministic semantic checks. Returns issues [{type, detail}]."""
    issues: list[dict] = []
    response = (state.final_response or "").strip()
    if not response:
        return issues

    # 1. filler/platitude
    lower = response.lower()
    if any(p in lower for p in PLATITUDE_PATTERNS):
        issues.append({"type": "platitude", "detail": "回复含空话套话"})
    if len(response.split()) <= 3 and response in FILLER_WORDS:
        issues.append({"type": "too_short", "detail": "回复过短，可能未完成任务"})

    # 2. ungrounded citation: cited paper id never read via read_paper
    if messages is not None:
        read_ids = _read_paper_ids_from_messages(messages)
        cited = _extract_cited_ids_from_response(response)
        if cited and read_ids:
            ungrounded = [c for c in cited if c not in read_ids and not c.isdigit()]
            if ungrounded:
                issues.append({"type": "ungrounded_citation",
                               "detail": f"引用论文未实际阅读: {ungrounded[:5]}"})
    return issues


SEMANTIC_PROMPT = """你是质检员。判断助手回复是否有质量问题，只输出 JSON 数组：
[{{"issue": "contradiction|not_answering|placeholder", "detail": "说明"}}]

- contradiction: 回复与下面"工具结果"明显矛盾（如工具说失败却回复成功）
- not_answering: 回复没有回答用户问题
- placeholder: 空话/模板/未真正干活
没有问题输出 []

用户: {user}
回复: {response}
工具结果(摘要): {tool_summary}
JSON:"""


def llm_check_semantic(llm, state, tool_summary: str = "") -> list[dict]:
    """One cheap LLM call; returns issues. Never raises."""
    prompt = SEMANTIC_PROMPT.format(
        user=(state.user_input or "")[:800],
        response=(state.final_response or "")[:1500],
        tool_summary=(tool_summary or "")[:1200],
    )
    try:
        raw = llm.complete([{"role": "user", "content": prompt}], max_tokens=200)
    except Exception:
        return []
    data = _parse_semantic_json(raw)
    return data


def _parse_semantic_json(raw: str) -> list[dict]:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text).rstrip("`").strip()
    try:
        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    valid = {"contradiction", "not_answering", "placeholder"}
    out = []
    for item in data:
        if isinstance(item, dict) and item.get("issue") in valid:
            out.append({"type": item["issue"],
                        "detail": (item.get("detail") or "")[:200]})
    return out


def run_semantic_check(state, messages=None, llm=None,
                       tool_summary: str = "") -> list[dict]:
    """Rule checks always; LLM check when provided. Returns issues."""
    issues = rule_check_semantic(state, messages)
    if llm is not None:
        issues += llm_check_semantic(llm, state, tool_summary)
    return issues


# ── Tests (deterministic, no LLM needed) ──

def _test():
    # Guardrail tests (from guardrail.py)
    from research_agent.guardrail import guardrail
    from research_agent.models import Action
    assert guardrail(Action(action="shell_exec", query="rm -rf /"))
    assert guardrail(Action(action="shell_exec", query="python test.py")) is None

    # Validator tests
    r = validate_shell_output({"success": False, "stderr": "error", "returncode": 1})
    assert not r.passed
    assert r.data["retry_hint"]

    r2 = validate_retrieval_result({"found": 0})
    assert r2.data["hint"]

    # Response validation
    s = AgentState(user_input="test", final_response="see paper:abc123-def456 for details")
    s2 = validate_response(s)
    # Response validator works deterministically
    assert s2.confidence is not None
    print("All tests passed")


if __name__ == "__main__":
    _test()