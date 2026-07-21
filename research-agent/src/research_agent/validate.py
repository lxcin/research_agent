"""Response validation: hallucination detection, citation verification, tool output validation."""
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