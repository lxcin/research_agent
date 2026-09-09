"""Tool parameter validation — catches LLM malformed calls before dispatch."""


TOOL_REQUIRED_PARAMS = {
    "shell_exec":    ["command"],
    "file_write":    ["path"],
    "file_read":     ["path"],
    "file_edit":     ["path", "old_string", "new_string"],
    "file_grep":     ["pattern"],
    "file_glob":     ["pattern"],
    "check_tasks":   [],
    "spawn_subagent": ["subtasks"],
}


def validate_tool_params(name: str, params: dict) -> str | None:
    """Check that required params exist and are non-empty. Returns error message or None."""
    required = TOOL_REQUIRED_PARAMS.get(name)
    if required is None:
        return None  # unknown tool, let dispatch handle it

    for key in required:
        val = params.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            return f"Missing required parameter: {key}"

    # Type checks
    if name == "subagent" or name == "spawn_subagent":
        subtasks = params.get("subtasks", [])
        if not isinstance(subtasks, list) or len(subtasks) == 0:
            return "subtasks must be a non-empty list"

    return None
