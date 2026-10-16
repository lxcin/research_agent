# Changelog

All notable changes to PaperPilot. Versioning follows the iteration milestones
(V1 → V4) rather than strict semver.

## [Unreleased]

- Archive completed tool arguments and full results independently of model
  history, including small outputs; retain current source excerpts and let
  tools declare older-result summaries through typed context projections.
- File tools instruct the agent to inspect current implementations before edits.
  Reads expose pagination and source ranges; edits return read-back verification,
  completion state, exact change ranges and before/after content digests.

- Make resume-segment snapshot handoff replayable across cursor publication and
  reference retirement; preserve old evidence until the new cursor commits.
- Persist cancellation during network recovery waits instead of leaving stale
  waiting status in execution inspection.
- Return confirmed tool network failures as durable observations for the agent;
  retain reconciliation for uncertain side effects. Model transport suspension
  remains separate from tool failures.

**Durable network suspension and step recovery for the default kernel.**

- Persist model selection, pending tools and answer phases before interruption;
  resume a model request without spending another thinking round.
- Retry transient failures with cancellable backoff, then save `waiting_network`.
  CLI/API retry the same execution twice more with bounded waits.
- Reuse committed tool results without repeating effects or approval; retain
  uncertainty checks for started calls. Record completion workspace evidence
  for recovery after a process dies before the loop advances.
- Keep the original file baseline across unchanged resumes; defer final E while
  suspended. Human workspace changes create a fresh segment and replan context.
- Expose execution status and stream `reply_reset` when replacing an interrupted
  answer. Permanent credential errors do not enter network recovery.

**Workspace baselines follow actual execution, including edits between turns.**

- A safe new turn snapshots the current working files and index before retiring
  old undo evidence. Missing old E no longer blocks admission; unfinished
  background writers, quarantine and applying/partial undo still do.
- Unapplied conflict plans expire on successful rollover, invalidating old
  tokens while retaining conflict files independently for manual cleanup.
- Snapshot capture without durable E is reported as `undo_unavailable`. Old
  delivery records are retired after new S; late callbacks cannot revive them.
- Shared chats remain serial by default; independent worktrees remain opt-in.
  Completion keeps actual files without creating an automatic Git commit.

**`file_grep`: ripgrep fast path with pure-Python fallback.**

### Changed
- `file_grep` now uses **ripgrep** (`rg --json`) when available on `PATH`
  (parallel, skips binary, `--no-ignore` for full-tree parity, `.git` excluded,
  `--max-filesize 500K`, 30-match cap), and falls back to the previous pure-Python
  glob+regex scan when `rg` is missing/errors. The tool result gains a `backend`
  field; `rg` is optional (zero new dependency).

**Session-scoped sandbox: reuse one sandbox across commands (OpenAI SandboxAgent-inspired).**

### Added
- `SandboxSession` (`sandbox.py`) — a live execution environment reused across
  commands. Docker sessions start **one long-lived container** (`docker run -d
  ... sleep infinity`, same hardening flags) and run commands via `docker exec`;
  local sessions are lightweight handles. Lifecycle: `start/exec/stop/close`,
  `is_alive()`, context-manager, atexit teardown.
- `shell.session: ephemeral | persistent` (config) + `shell.idle_timeout`:
  - `ephemeral` — one session per run, created lazily on first `shell_exec`,
    reused for the rest of the run, closed at run end (SDK-owned).
  - `persistent` — one session per **workspace**, living for the process and
    reused across runs, reclaimed on idle timeout / death (developer-owned).
- `get_session()/end_run()/close_all_sessions()` resolution helpers; run
  lifecycle wired in `agent.run_agent` (ephemeral session closed in a `finally`).

### Changed
- `shell_exec` (foreground) now routes through a **reused session** instead of a
  per-command container. Background tasks keep the one-shot path (they may
  outlive the run).
- `brief.py` reports the session mode alongside the backend.

### Notes
- Backward compatible: `run_command` / `build_docker_argv` / `resolve_backend` /
  `SandboxConfig` unchanged; `run_command` remains the one-shot primitive.
- Rationale: a session-scoped sandbox avoids paying `docker run` (and a git
  snapshot) per command for a batch of N commands, while keeping ephemeral's
  clean-start default and adding an explicit persistent mode for stateful work.

**Turn-aligned workspace checkpoints.**

### Added
- `shell.checkpoint: turn | command | off` (default `turn`) + `get_checkpoint_mode()`
  (legacy boolean still accepted: `true`→turn, `false`→off).
- `checkpoint.snapshot_for_run()` — creates the run-level checkpoint before the
  loop; `run_agent` calls it once per run (shell plugin enabled).
- `create_checkpoint(..., dedupe=True)` — skips creating a ref when the workspace
  tree is unchanged (returns the previous ref with `skipped=True`).

### Changed
- Checkpoint granularity moved from **per-command** to **per-turn** (default):
  one snapshot per run, shared by all shell commands in that run; `_maybe_checkpoint`
  now reports the run ref instead of creating one per command. `command` keeps the
  old finest-grained behavior; `off` disables snapshots.

### Notes
- Rollback boundary for `turn` is "before this request" — finer per-command undo
  requires `shell.checkpoint: command`.

**Task planning — plan-as-memory (episodic/procedural).**

### Added
- `plan` plugin: `plan_update` (register a structured, ordered plan before
  multi-step work; each step has status + `depends_on` + optional `tools`),
  `plan_search` (recall similar past plans by goal), and `plan_execute`
  (DAG scheduling: independent ready steps run in parallel via sub-agents).
  10 plugins / 22 tools.
- `research_agent/plan.py` — `Plan`/`PlanStep` model, per-project persistence
  under the data dir (`projects/<id>/plans/*.json`, never the git workspace),
  and deterministic keyword-overlap `search_plans`.
- `research_agent/plan_executor.py` — dependency scheduler: `ready_steps()`
  (pure) + `execute_plan()` (parallel ready batches, marks done/blocked, detects
  cycles); dispatch is injected so the scheduler is unit-testable with a fake
  dispatcher. `plan_execute` wires dispatch to parallel sub-agents.
- **Decision fields** — `PlanStep.reason` (why this step) + `Plan.decisions`
  (key decisions) + `Plan.outcome` + `Plan.tools_used`.
- **Automatic episodic write** — `agent._maybe_save_episode()` records a task
  episode every run: the explicit plan if one was registered (enriched with the
  tool path actually used), else a heuristic episode from input + tool path +
  response. Gated by `plan.auto_episode` (default true); skips runs with no tool
  activity. Tool path is collected host-side from `tool_start` events.
- **Vector-first agentic recall** — episodes are indexed with an embedding at
  save time; `search_plans` ranks vector-first with a jieba/keyword fallback.
  The agent supplies the query (agentic recall). Embedding helper extracted to
  `research_agent/embedding.py` (`RESEARCH_AGENT_EMBEDDING`; legacy
  `RESEARCH_AGENT_MEMORY_VECTOR` honored).
- **Cross-project episodic recall** — `plan_search` scans **all projects'
  episodes** by default (`list_all_plans`), with the current project as a
  tie-break bonus (`prefer_workspace`); `scope=project` restricts to the current
  project. Rationale: a same-project task's context is usually already in the
  conversation, so the value is remembering other projects.
- **Lightweight local episodic DB** — episodes now live in one **SQLite** file
  (`data_dir/episodes.db`) instead of per-project JSON files: embeddings stored as
  float32 BLOBs, one row per episode (append-only, `uuid` id, indexes on
  `created_at`/`workspace`). Recall is vector-first via a cached **numpy** matrix
  multiply (`_score`) with a jieba-token fallback — no vector DB / no server.
  Best-effort one-time import of legacy `projects/*/plans/*.json`.
- **Async episodic distillation** (`research_agent/plan_pipeline.py`) — the run-end
  automatic memory write now happens **off the request thread**: a small-model
  `distill_episode` (goal / decisions+reason / steps / outcome) when
  `plan.distill` is on, else a heuristic fallback, then `save_plan`. `drain()`
  for tests.

### Removed
- **Tier B semantic memory (whole subsystem)** — `memory/{models,storage,vector,
  source,extractor,pipeline,retrieve,tier_b,rerank}.py`, `tools/builtin/memory_tool.py`
  (`memorize` / `search_memory`), the `memory` plugin, `agent._maybe_distill` /
  `_persist_pending_task`, `diagnostics/feedback.py`, the context memory hint, and
  `config.get_memory_config`. Automatic task memory is now episodic
  (`research_agent.plan`). Core conversation persistence (`memory/__init__.py`)
  is kept.

### Changed
- Plugin/tool count: 10 / 22 → **9 / 20**.
- System prompt tool rules: multi-step/decomposable tasks register a plan via
  `plan_update` (with `reason`/`decisions`) then run it with `plan_execute`;
  before a possibly-done task, the agent formulates a query and calls
  `plan_search` for prior episodic memory (approach/decisions/pitfalls).

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


### 2026-10-16 文件读写与上下文契约 v3
- 文件读取改为行范围与超长行字符列续读；严格 UTF-8、64 MiB 上限、有界内存流式哈希与读取一致性检查。
- 编辑显式携带 expected_version，校验会话/工作区内实际已读覆盖；新建声明 absent，过期拒绝并要求主动读取。
- harness 仅追加过期通知，不恢复源码；轮内按水位及最小节省量批量折叠。
- 编辑回执增加前后 unified diff、完整差异引用、版本与声明意图，区分完成、失败、部分完成及测试验证。
