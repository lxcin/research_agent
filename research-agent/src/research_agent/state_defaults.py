"""Compatibility field bundle injected by the application state constructor.

The container does not declare these fields. Plugins may add or unload fields.
Legacy fields remain here until their consumers migrate to plugin-owned bundles.
"""
from research_agent.state_components import StateComponent


def validate_constraints(value):
    if any(not isinstance(item, str) for item in value):
        raise TypeError("constraints must contain strings")


DEFAULT_STATE_FIELDS = (
    StateComponent("constraints", list, encode=list, decode=list, owner="core", lifetime="session", value_type=list,
                   validate=validate_constraints),
    StateComponent("attachment_blocks", list),
    StateComponent("user_input", lambda: ""),
    StateComponent("workspace_dir", lambda: ""),
    StateComponent("project_workspace_dir", lambda: ""),
    StateComponent("session_branch", lambda: ""),
    StateComponent("active_chat_id", lambda: ""),
    StateComponent("active_project", lambda: None),
    StateComponent("caps", dict),
    StateComponent("retrieved_chunks", list),
    StateComponent("retrieved_context", list),
    StateComponent("retrieval_sufficient", lambda: False),
    StateComponent("search_query", lambda: ""),
    StateComponent("needs_retrieval", lambda: True),
    StateComponent("citations", list),
    StateComponent("confidence", lambda: "uncertain"),
    StateComponent("retry_count", lambda: 0),
    StateComponent("final_response", lambda: ""),
    StateComponent("termination_reason", lambda: ""),
    StateComponent("error", lambda: ""),
    StateComponent("needs_compression", lambda: False),
    StateComponent("conversation_turns", list),
    StateComponent("compressed_summaries", list),
    StateComponent("memory_units", list),
    StateComponent("round_count", lambda: 0),
    StateComponent("errors", list),
    StateComponent("sections", list),
    StateComponent("pending_proposals", list),
    StateComponent("_pending_confirms", dict, fork=lambda value: {}),
)

# Host resource dictionaries retain reference access; data lists use detached reads.
from dataclasses import replace
DEFAULT_STATE_FIELDS = tuple(replace(spec, owner=spec.owner or "core") for spec in DEFAULT_STATE_FIELDS)

from research_agent.models import Project
_SESSION_FIELDS = {"constraints", "active_chat_id", "workspace_dir", "project_workspace_dir", "session_branch", "active_project", "conversation_turns", "compressed_summaries"}
DEFAULT_STATE_FIELDS = tuple(replace(spec,
    lifetime="session" if spec.key in _SESSION_FIELDS else "request",
    copy_on_read=spec.copy_on_read or isinstance(spec.factory(), list),
    value_type=(Project, type(None)) if spec.key == "active_project" else type(spec.factory()))
    for spec in DEFAULT_STATE_FIELDS)
