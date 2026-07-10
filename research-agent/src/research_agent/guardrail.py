"""Action guardrail: blocks dangerous operations before execution."""
from research_agent.models import Action

DANGEROUS_COMMANDS = [
    "rm -rf", "drop table", "delete from", "shutdown",
    "format", "mkfs", "dd if=", "> /dev/"
]


def guardrail(action: Action) -> str | None:
    if action.action in ("shell", "exec", "delete", "drop"):
        return f"操作被拦截: '{action.action}' 是危险操作，需要人工确认。"

    for cmd in DANGEROUS_COMMANDS:
        if cmd in action.query.lower():
            return f"查询被拦截: 包含危险命令 '{cmd}'"

    return None