"""Internal quality gate + dashboard for PaperPilot.

Collects deterministic build/test metrics, evaluates them against thresholds
(gates), and renders a self-contained HTML dashboard with history trends.

Entry points:
  - CLI:  `research-agent quality`
  - Module: `python -m research_agent.quality.gate`
  - API:  `get_latest_report()` in report.py

Submodules are imported lazily by callers (keep this package import-light so
`python -m research_agent.quality.gate` does not re-import itself).
"""
__all__ = ["collect", "gate", "report"]
