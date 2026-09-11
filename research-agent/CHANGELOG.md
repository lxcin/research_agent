# Changelog

All notable changes to PaperPilot. Versioning follows the iteration milestones
(V1 → V4) rather than strict semver.

## [4.0.0] — 2026-09

**Direction change: research tool → general-purpose personal assistant.
Architecture: replaceable kernel + pluggable capability plugins.**

### Removed
- Paper retrieval stack: `search_papers` / `read_paper` / `retrieve` tools,
  `paper_store.py`, `retrieval.py` (grep), `search.py`, `arxiv_pdf.py`.
- Historical storage/API: `knowledge_graph.py`, `ingestion.py`, `vector_store.py`,
  `store.py` and the paper/upload/graph HTTP endpoints.
- `router.py` (topic extraction) and the `literature-review` skill.
- Web (React/Vite) and Desktop (pywebview) frontends; `desktop.py`,
  `paperpilot.spec`, `Dockerfile.frontend`, `nginx.conf`. Backend is now pure API.
- `features/` package (superseded by tool plugins).

### Added
- **Replaceable agent kernel** (`runtime.py`): `AgentRuntime` / `RuntimeContext` /
  `FunctionCallingRuntime`; `run_agent` is now a thin host shell delegating to the
  kernel. Governance (guardrail/HITL) and write-validation are injected via
  `RuntimeContext` hooks — the kernel knows no concrete tools.
- **Two-level tool plugin system** (`tools/schema.py`, `tools/__init__.py`):
  `ToolSchema` (agent-facing granularity) vs `ToolPlugin` (lifecycle unit) with
  install / enable / disable / uninstall, dependency checks and disk-footprint
  cleanup. 6 plugins: filesystem, shell, subagent (core); memory, diagnostics,
  mcp (optional).
- **CLI `plugin` command**: `plugin list | enable | disable | uninstall`.
- **Conversation-level agentic RAG memory**: the agent autonomously formulates
  retrieval queries from dialogue and calls the `search_memory` tool
  (memory plugin), instead of injected context.
- **Evaluation scripts** (reproducible):
  `tests/eval_memory_recall.py` (retriever, hybrid vs keyword),
  `tests/eval_memory_agentic.py` (hard-negative corpus + LLM query expansion),
  `tests/eval_agentic_rag.py` (conversation-level agentic behavior).

### Changed
- `context.py` system prompt rewritten for a general assistant; memory guidance
  inlined (retrieval is tool-driven).
- Workspace binding slimmed: no auto-created `papers/`/`experiments/` dirs, no
  topic extraction; default clean working directory.
- Dependencies trimmed; `chromadb` + `sentence-transformers` moved to an optional
  `vector` extra.
- Local `config.yml` gitignored.

### Fixed
- `ToolRegistry.install_plugin` idempotent path ignored the config enable state,
  so `register_builtins()` silently re-enabled a disabled plugin (e.g. memory).

### Verified
- 209 deterministic tests passing (MockLLM, no network/API key).
- Memory retrieval eval (48 units / 27 queries, hard negatives): hybrid
  Recall@5 62.8% → 81.7%, MRR 0.56 → 0.78 (paraphrase R@5 56% → 89%).
- Conversation-level agentic eval (8 scenarios): retrieval-decision 100%,
  query-hit 100%, answer grounding 17% (no memory) → 83% (with memory),
  over-retrieval 0%.

## [3.0.0] — 2026-08
- Workspace-based project model (filesystem instead of SQLite for projects).
- Structured SSE protocol; chat-centric frontend.
- Governance depth: guardrail + HITL + `auto_validate` feedback loop.
- Function-calling agent loop (replacing self-parsed JSON).

## [2.0.0] — 2026-07
- litellm native function calling; skill = context injection.
- arXiv search; TF-IDF semantic chunking; model-adaptive context window.
- React + TypeScript frontend.

## [1.0.0] — 2026-07
- Initial: LangGraph-based research agent, SQLite + ChromaDB, hybrid retrieval.
