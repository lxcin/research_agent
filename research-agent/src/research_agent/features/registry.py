"""Feature registry — declarative catalogue of optional subsystems.

Every optional, self-contained module group is registered here with the files,
tests, data dirs and core wiring points it owns. This is the single source of
truth for:

  - `feature list`           : show subsystems + enabled state
  - `feature enable|disable` : toggle at runtime via config
  - `feature uninstall`      : disable + delete owned assets (guarded)

A feature is uninstallable only when nothing outside its owned set still
imports it — uninstall runs a reference scan first and refuses with the exact
core wiring points that still depend on it (safe by construction, never leaves
core half-broken).
"""
from dataclasses import dataclass, field
import os


@dataclass
class Feature:
    id: str
    label: str = ""
    core: bool = False
    depends: list[str] = field(default_factory=list)
    # Owned source assets (research-agent/src/research_agent/... relative to src)
    owned_files: list[str] = field(default_factory=list)
    owned_packages: list[str] = field(default_factory=list)  # import paths e.g. research_agent.diagnostics
    test_files: list[str] = field(default_factory=list)
    data_dirs: list[str] = field(default_factory=list)
    # Known core wiring points (for diagnostics messaging when uninstall is blocked)
    wiring_points: list[str] = field(default_factory=list)
    default_enabled: bool = True


# ── Feature catalogue ───────────────────────────────────────────────────────

FEATURES: dict[str, Feature] = {
    # Core — never uninstallable.
    "core": Feature(
        id="core", label="核心 harness（agent loop / 工具注册 / 治理 / 项目工作区 / 论文grep）",
        core=True,
        owned_files=["research_agent/agent.py", "research_agent/tools/__init__.py",
                     "research_agent/tools/schema.py", "research_agent/tools/builtin/__init__.py",
                     "research_agent/tools/builtin/filesystem.py",
                     "research_agent/tools/builtin/retrieve.py", "research_agent/tools/git_tool.py",
                     "research_agent/tools/validate_params.py",
                     "research_agent/guardrail.py", "research_agent/context.py",
                     "research_agent/models.py", "research_agent/config.py",
                     "research_agent/project_manager.py", "research_agent/paper_store.py",
                     "research_agent/retrieval.py", "research_agent/router.py",
                     "research_agent/search.py", "research_agent/store.py",
                     "research_agent/trace_log.py", "research_agent/llm.py",
                     "research_agent/validate.py", "research_agent/skill_loader.py",
                     "research_agent/server.py", "research_agent/cli.py"],
    ),
    # V4 Tier B personal memory (long-term memory). Note: the legacy
    # conversation-persistence API lives in research_agent.memory.__init__ and is
    # core; Tier B is the models/storage/vector/source/extractor/pipeline/retrieve
    # modules + the memorize tool + data dir.
    "memory_tier_b": Feature(
        id="memory_tier_b",
        label="Tier B 个人长期记忆（MemoryUnit/提炼/召回/memorize 工具）",
        owned_packages=["research_agent.memory.models", "research_agent.memory.storage",
                        "research_agent.memory.vector", "research_agent.memory.source",
                        "research_agent.memory.extractor", "research_agent.memory.pipeline",
                        "research_agent.memory.retrieve", "research_agent.memory.tier_b"],
        owned_files=["research_agent/tools/builtin/memory_tool.py"],
        test_files=["tests/test_memory_units.py", "tests/test_memory_write_path.py",
                    "tests/test_memory_read_path.py", "tests/test_polish.py"],
        data_dirs=["memory"],
        wiring_points=["agent._maybe_distill", "agent._persist_pending_task",
                       "context.build_context (Global Memory 注入)",
                       "tools/builtin/__init__.register_builtins (memorize)"],
    ),
    # Diagnostics subsystem (fault monitoring + developer reports).
    "diagnostics": Feature(
        id="diagnostics",
        label="故障检测与开发者报告（事件流/监控/scan/report/API/CLI）",
        depends=["memory_tier_b"],  # diagnostics/feedback.py writes DEAD_END into Tier B
        owned_packages=["research_agent.diagnostics"],
        test_files=["tests/test_diagnostics.py", "tests/test_diagnostics_phase_e.py"],
        data_dirs=["logs", "diagnostics"],
        wiring_points=["agent.run_agent (recorder/monitor 包装)",
                       "cli.main/diagnose", "server /api/diagnostics"],
    ),
    # MCP external tools loader.
    "mcp": Feature(
        id="mcp", label="MCP 外部工具（stdio JSON-RPC 客户端）",
        owned_files=["research_agent/tools/mcp_loader.py"],
        test_files=["tests/test_mcp.py"] if False else [],
        data_dirs=[],
        wiring_points=["agent.run_agent (MCP autoload)"],
    ),
    # Knowledge graph + historical paper upload/DB APIs (server-only endpoints).
    "knowledge_graph": Feature(
        id="knowledge_graph",
        label="知识图谱 + 历史上传/论文 DB API（/api/graph, /api/upload, ingestion）",
        owned_files=["research_agent/knowledge_graph.py", "research_agent/ingestion.py",
                     "research_agent/vector_store.py"],
        test_files=["tests/test_knowledge_graph.py", "tests/test_ingestion.py"],
        wiring_points=["server /api/graph*", "server /api/upload/pdf", "server /api/papers"],
        default_enabled=True,
    ),
}


def get_feature(feature_id: str) -> Feature | None:
    return FEATURES.get(feature_id)


def list_features() -> list[Feature]:
    return sorted(FEATURES.values(), key=lambda f: (not f.core, f.id))


def is_core(feature_id: str) -> bool:
    f = FEATURES.get(feature_id)
    return f is not None and f.core


# ── Config-backed enabled state ─────────────────────────────────────────────

def _features_config() -> dict:
    from research_agent.config import load_config
    cfg = load_config()
    feats = cfg.get("features", {})
    return feats if isinstance(feats, dict) else {}


def is_enabled(feature_id: str) -> bool:
    """Resolve runtime enabled state (config overrides catalogue default)."""
    f = FEATURES.get(feature_id)
    if f is None or f.core:
        return True
    feats = _features_config()
    entry = feats.get(feature_id, {})
    if isinstance(entry, dict) and "enabled" in entry:
        return bool(entry["enabled"])
    return f.default_enabled


def set_enabled(feature_id: str, enabled: bool) -> bool:
    """Persist enabled state into config.yml features section. Returns False if unknown/core."""
    f = FEATURES.get(feature_id)
    if f is None or f.core:
        return False
    from research_agent.config import get_config_path, load_config
    cfg = load_config()
    feats = cfg.setdefault("features", {})
    if not isinstance(feats, dict):
        feats = {}
        cfg["features"] = feats
    entry = feats.get(feature_id, {})
    if not isinstance(entry, dict):
        entry = {}
    entry["enabled"] = enabled
    feats[feature_id] = entry
    try:
        with open(get_config_path(), "w", encoding="utf-8") as fh:
            import yaml
            yaml.safe_dump(cfg, fh, allow_unicode=True, sort_keys=False)
        return True
    except Exception:
        return False


def dependencies_met(feature_id: str) -> tuple[bool, list[str]]:
    """Check feature deps are enabled. Returns (ok, unmet_deps)."""
    f = FEATURES.get(feature_id)
    if f is None:
        return False, []
    unmet = [d for d in f.depends if not is_enabled(d)]
    return (not unmet, unmet)


def dependents(feature_id: str) -> list[str]:
    """Enabled features that declare feature_id as a dependency.

    Used to block uninstalling something still depended on by an enabled feature.
    """
    if feature_id not in FEATURES:
        return []
    return [fid for fid, f in FEATURES.items()
            if not f.core and is_enabled(fid) and feature_id in f.depends]


# ── Reference scan (uninstall safety) ───────────────────────────────────────

def _owned_module_names(f: Feature) -> list[str]:
    """Importable module names fully owned by this feature (self-exclusion set)."""
    names = set()
    for pkg in f.owned_packages:
        names.add(pkg)
    for fname in f.owned_files:
        names.add(fname.replace("/", ".").replace(".py", ""))
    return sorted(names)


def scan_references(feature_id: str, src_root: str) -> list[str]:
    """Return core files with a MODULE-LEVEL (top-level) import of an owned module.

    Function-local imports are excluded: when the feature is disabled the call
    path is gated and never executes, so file deletion is safe. Top-level
    imports would crash core at import time → those block uninstall.
    """
    import ast as _ast
    f = FEATURES.get(feature_id)
    if f is None or f.core:
        return []
    owned_names = _owned_module_names(f)

    offenders: list[str] = []
    for dirpath, _dirs, filenames in os.walk(src_root):
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, src_root).replace("\\", "/")
            mod = rel[:-3].replace("/", ".")
            if mod in owned_names or mod.startswith(tuple(n + "." for n in owned_names)):
                continue  # owned file itself
            try:
                tree = _ast.parse(open(full, "r", encoding="utf-8").read())
            except (OSError, SyntaxError):
                continue
            imports = []
            for node in tree.body:  # module-level only (top-level imports)
                if isinstance(node, _ast.ImportFrom) and node.module:
                    imports.append(node.module)
                elif isinstance(node, _ast.Import):
                    imports.extend(a.name for a in node.names)
            if any(n == imp or imp.startswith(n + ".")
                   for n in owned_names for imp in imports):
                offenders.append(rel)
    return sorted(set(offenders))
