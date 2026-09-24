# Changelog

All notable changes to PaperPilot. Versioning follows the iteration milestones
(V1 → V4) rather than strict semver.

## [4.3.0] — 2026-09

**Capabilities: web access, self-evolution, runtime evaluation/quality, runtime brief.**

### Added
- **`web` plugin** (`tools/builtin/network.py`): `web_fetch` / `web_search` behind an
  SSRF-aware URL policy (scheme allowlist, explicit IP-range guard incl.
  IPv4-mapped/6to4/NAT64 unwrap, per-hop redirect re-validation, byte/char/time caps,
  loopback-only `allow_localhost`). Multi-backend fetch (direct → Jina Reader) and
  search (Exa/Tavily/Serper/DDG ordered routing) + `web-doctor`. Opt-in, read-only.
- **`evolve` plugin + project-experience report**: distil experience into versioned,
  toggleable user skills (project report → classify → build → static review → human
  approval → provenance records). Usage tracking + utility A/B; `research-agent evolve
  list|report|skills|experience|usage|enable|disable`. Plugin authoring deferred
  (see `docs/PLUGIN_CONTRACT.md`).
- **`telemetry` plugin**: runtime evaluation as an **AgentRuntime observation layer**
  (`MeteredRuntime`, kernel loop untouched) — tokens, cache-aware cost, latency,
  tool path, phase split; `usage_report`/`usage_query` tools.
- **Runtime audit + quality gate**: `diagnostics/audit.py` framework scorecard
  (tool health / convergence / completion / economy); `quality/` gate
  (pytest+coverage+security thresholds) + self-contained HTML scoreboard, wired into CI.
- **Runtime brief** (`brief.py`): runtime-derived environment/principal/contract
  injected before work (correct across OSes, not hardcoded).
- **MCP CLI**: `research-agent mcp add/list/remove/test` with `name`/`env` config; tools
  auto-register as `mcp_*`.
- Docs: `NETWORK_PLUGIN`, `QUALITY`, `SELF_EVOLUTION`, `EVALUATION`, `PLUGIN_CONTRACT`.

### Changed
- **Memory read path hardened**: per-turn retrieval cap + near-duplicate query
  rejection + confidence bucket (strong/weak/none) with guidance; bge-zh query
  instruction + precomputed query embeddings.
- **Deterministic loop breaker** (`runtime._ProgressGuard`): repeated identical calls
  / consecutive failures feed back **inside the tool result** (`_loop_hint`) — no extra
  messages, cache-friendly. Replaces mid-turn context trim / wall-clock budget.

### Fixed
- Windows subprocess decoding (`errors="replace"`) across shell/git/quality/MCP paths.
- Preserve provider cache fields in LLM usage + **bill cache-read tokens cheaper**.
- Neutral shell failure hint (no longer misleadingly points at `file_edit`).
- IPv6 SSRF false positive (`2001::/23` no longer blanket-blocked).

### Verified
- 392 deterministic tests (MockLLM, no network/key); `research-agent quality` 6/6 PASS.
- Real-task telemetry: tool success / cache hit / cost / tool-path captured end-to-end.

## [4.2.1] — 2026-09

**Aggregate/set queries: agent enumerates by `kind` instead of semantic top-5.**

### Changed
- `context.py` memory hint now instructs the agent: for "列出所有/有哪些某类
  信息" queries, call `search_memory(kind=<type>, limit=20)` (structured
  enumeration) rather than a semantic top-5. `search_memory` already accepts
  `kind`; this is a prompt/agentic-strategy change.

### Measured
- Root cause of low aggregate R@5 diagnosed: R@5 caps coverage when |gold|>5,
  and semantic top-k cannot enumerate a whole category.
- Conversation-level eval (`tests/eval_agentic_rag.py`, now with aggregate
  scenarios): set-query **coverage 0% (no memory) → 100% (with memory)**; the
  agent was observed calling `search_memory(kind="preference"/"dead_end",
  limit=20)`.

## [4.2.0] — 2026-09

**Memory retrieval: vector-first + MMR diversity re-ranking (data-driven).**

### Added
- `memory/rerank.py` — `mmr_select`: model-free MMR (λ relevance/diversity)
  re-ranking over normalized embeddings; unit-testable without Chroma.
- `memory/vector.py` — `encode_query`, `query_with_embeddings` (candidate
  embeddings for re-ranking).
- `tests/test_rerank.py` (8 tests); `tests/eval_memory_agentic.py` now reports
  a keyword/vector/hybrid core table, a weighted-RRF sweep, and an MMR sweep.

### Changed
- `memory/tier_b.MemoryManager.retrieve` now uses **vector-first + MMR (λ=0.7)**
  with keyword search only as fallback. Equal-weight keyword+vector RRF was
  measured to be *worse* than plain vector here (keyword noise), so it was
  dropped from the vector path. `MMR_LAMBDA` configurable
  (`RESEARCH_AGENT_MMR_LAMBDA`).

### Measured (48 units / 27 queries, hard negatives)
- keyword-only R@5 62.8% → vector R@5 89.8% → **vector + MMR R@5 91.6%**;
  aggregate-query R@5 45% → 55%; MRR 0.86.
- Evidence recorded in `tests/eval_memory_agentic.py` (reproducible).

## [4.1.0] — 2026-09

**Git-based file-change proposals: review changes as a diff and keep/undo per
file (opencode / Claude Code style).**

### Added
- `proposal.py` — `ProposalManager`: collect changed files as diff proposals
  (status / +/- lines / unified diff), then `keep` (commit) or `undo`
  (restore or delete) per file or all. Uses the workspace git repo.
- CLI proposal review after each turn: `keep all | undo all | keep 1,3 |
  undo 2 | diff N | done`; agent file changes are no longer auto-finalized.
- `AgentState.pending_proposals` + `proposal` events on the event stream.
- `tests/test_proposals.py` (8 tests) over real temp git repos.

### Notes
- If the workspace is not a git repo, no proposal is produced (changes are
  written as before) — graceful fallback.
- Dangerous shell still goes through HITL; proposals cover file writes/edits.

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
