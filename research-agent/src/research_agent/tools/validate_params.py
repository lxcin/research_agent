"""Schema-driven, offline-only tool parameter validation."""
from jsonschema import Draft202012Validator


def check_schema(schema):
    Draft202012Validator.check_schema(schema)
    def walk(value):
        if isinstance(value, dict):
            for key in ("$ref", "$dynamicRef"):
                if key in value and not value[key].startswith("#"):
                    raise ValueError("External schema references are not supported")
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(schema)


def validate_schema_params(schema, params):
    if not isinstance(params, dict):
        return "Tool parameters must be an object"
    errors = sorted(Draft202012Validator(schema).iter_errors(params), key=lambda error: str(list(error.path)))
    if not errors:
        return None
    error = errors[0]
    path = ".".join(str(item) for item in error.path) or "$"
    return f"Invalid parameter {path}: {error.message}"


def validate_tool_params(name, params, registry=None):
    """Compatibility adapter; no hardcoded tool-specific requirements."""
    if registry is None:
        from research_agent.tools import get_registry
        registry = get_registry()
    tool = getattr(registry, "tools", {}).get(name)
    if tool is None:
        return None if isinstance(params, dict) else "Tool parameters must be an object"
    return validate_schema_params(tool.parameters, params)
