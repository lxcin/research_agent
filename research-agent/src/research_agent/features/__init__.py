"""Feature registry — optional subsystems catalogue + enable/disable/uninstall."""
from research_agent.features.registry import (
    FEATURES, Feature, get_feature, list_features, is_core,
    is_enabled, set_enabled, dependencies_met, scan_references,
)
