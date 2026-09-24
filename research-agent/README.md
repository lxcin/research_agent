# PaperPilot — Personal-Assistant Agent Harness

PaperPilot is a from-scratch AI agent framework (not a LangGraph/Dify wrapper) built around two ideas: **a replaceable agent kernel** and **everything-else-is-a-capability-plugin**. It runs an LLM function-calling loop with deterministic governance, pluggable tools, conversation-level agentic-RAG memory, and developer-facing observability.

PaperPilot 是一个自研的 AI Agent 框架（非 LangGraph/Dify 套壳），围绕两个理念设计：**可替换的 Agent 内核** 与 **一切皆能力插件**。它运行 LLM function-calling 循环，内置确定性治理、可插拔工具、对话级 agentic-RAG 记忆，以及面向开发者的可观测性。

> **v4.3.0** — 联网插件 · 自进化(经验→技能) · 运行时评测/质量门禁 · 运行时简报 · MCP CLI

```
Host shell (agent.py)          lifecycle: workspace binding · diagnostics · post-run hooks
        │
        ▼
AgentRuntime (runtime.py)      REPLACEABLE kernel: loop + function calling
        │                        governance/validation injected via RuntimeContext
        ▼
Tool plugins (tools/)          filesystem · shell · subagent · memory · web · mcp · evolve · telemetry · diagnostics
```

---

## 亮点 (Highlights)

- **可替换内核 (Replaceable kernel)** — `AgentRuntime` host/strategy split: the loop knows no concrete tool; governance, tools and context arrive via `RuntimeContext`. Swap the loop (while-ReAct → other strategies) without touching capabilities.
- **两级工具插件 (Two-level plugins)** — `ToolSchema` (agent-facing granularity) vs `ToolPlugin` (engineering unit: cohesive domain + lifecycle). Runtime install / enable / disable / uninstall with dependency checks and disk-footprint cleanup. **9 plugins / 19 tools**.
- **纵深治理 + 循环守卫 (Governance + loop breaker)** — regex guardrail + path sandbox + HITL approval + post-write `py_compile/pytest` self-correction; a deterministic `_ProgressGuard` feeds repeated-call / error-streak feedback back **inside the tool result** (no extra messages, cache-friendly). All mock-testable, no API key/network.
- **对话级 agentic RAG 记忆 (Conversation-level agentic RAG)** — small-model distillation into typed `MemoryUnit`s; the agent autonomously formulates retrieval queries and calls `search_memory`; **vector-first recall + MMR** re-ranking; read path hardened (per-turn cap + near-duplicate rejection + confidence bucket).
- **联网能力插件 (Web plugin)** — `web_fetch` / `web_search` behind an **SSRF-aware URL policy** (scheme allowlist, IP-range guard incl. IPv4-mapped/6to4/NAT64 unwrap, per-hop redirect re-validation, size caps); multi-backend fetch (direct → Jina Reader) and search (Exa/Tavily/Serper/DDG routing) + `web-doctor`. Opt-in, read-only.
- **自进化 (Self-evolution)** — distil project experience into **versioned, toggleable user skills** (build → static review → human approval → provenance records), with usage tracking and utility A/B. Follows a build-vs-buy ladder (reuse/MCP first); plugin authoring deferred.
- **运行时评测 + 质量门禁 (Runtime eval + quality gate)** — `telemetry` (an **AgentRuntime observation layer**, loop untouched) records tokens / **cache-aware cost** / latency / tool paths; `audit` scores framework effectiveness; `quality` gates build+coverage+security and renders an HTML scoreboard, wired into CI.
- **运行时简报 (Runtime brief)** — a runtime-derived environment/principal/contract brief injected before work (correct across OSes, not hardcoded), to cut wasted environment-guessing rounds.
- **MCP 外部工具** — import external MCP servers via `research-agent mcp add/list/remove/test` (stdio; tools auto-register as `mcp_*`).
- **提案式文件改动 (Git-based change proposals)** — agent file edits are staged in the workspace git repo (not auto-committed); after each turn they are shown as a diff and the user decides `keep` (commit) or `undo` (restore/delete), per file or all at once — opencode / Claude Code style.
- **沙箱化命令执行 + 工作区回滚 (Sandboxed shell + rollback)** — `shell_exec` can run in a one-shot Docker container (network off, workspace-only mount, CPU/mem/pid caps); before each command the workspace is snapshotted (git), so its writes can be rolled back.

---

## 快速开始 (Quick Start)

```bash
git clone https://github.com/lxcin/research_agent.git
cd research_agent
pip install -e .

# 配置 API Key
export DEEPSEEK_API_KEY=sk-xxx      # 或写入 ~/research-agent-data/config.yml

# 交互式 CLI（主要入口）
research-agent chat

# 能力插件管理
research-agent plugin list
research-agent plugin disable memory

# 开发者诊断
research-agent diagnose

# 内部质量门禁（测试/覆盖率/安全 → PASS/FAIL + HTML 看板）
research-agent quality --open

# 运行时全链路审计：追踪→审查→评分（框架有效性）+ 报告
research-agent audit --report

# 运行时评测遥测：token/费用/时延/工具路径（telemetry 插件默认启用）
research-agent plugin list            # 查看 telemetry
# Agent 侧：usage_report / usage_query 工具

# MCP 外部工具：添加/查看/测试外部 MCP server（写入 skills/mcp.yml）
research-agent mcp add exa -- npx -y mcporter run exa
research-agent mcp list

# 自进化：经验报告 / 用户技能 / 全链路记录查询
research-agent evolve experience      # 打印项目经验报告
research-agent evolve skills          # 列出用户技能
research-agent evolve list            # 查询晋升记录（全链路审计）
research-agent evolve report          # 生成自进化报告

# 纯 API 服务（可选）
PYTHONPATH=src python -m uvicorn research_agent.server:app --host 0.0.0.0 --port 8050
# → /docs, /api/chat (SSE), /api/diagnostics, ...
```

**依赖**: Python 3.11+ · 可选向量层: `pip install -e ".[vector]"`（`sentence-transformers` + `chromadb`）

---

## 架构分层 (Architecture)

| 层 | 文件 | 职责 |
|----|------|------|
| **Host 外壳** | `agent.py` | workspace/project 绑定、插件装载、诊断包装、回合后横切（持久化/压缩/记忆提炼） |
| **Kernel 内核** | `runtime.py` | `AgentRuntime` 接口 + `FunctionCallingRuntime`（while 循环 + function calling + 通用收敛） |
| **Capabilities 插件** | `tools/` | `ToolSchema`（Agent 粒度）+ `ToolPlugin`（工程粒度，可装卸） |
| **Memory 记忆** | `memory/` | 对话持久化（core）+ Tier B 个人长期记忆 |
| **Diagnostics 诊断** | `diagnostics/` | 事件流 / 监控 / 扫描 / 报告 / 记忆回写 |

**内核契约**：`RuntimeContext` 注入 `llm / state / registry / emit`、前置审批钩子 `pre_tool_hook`、写入校验钩子 `on_tool_success`、可替换的 LLM 调用原语。内核永远不认识任何具体工具名。

---

## 能力插件 (Capability Plugins)

| 插件 | 工具 | 类型 | 说明 |
|------|------|------|------|
| `filesystem` | file_read / file_write / file_edit / file_glob / file_grep | core | 工作区文件操作，路径沙箱 |
| `shell` | shell_exec / check_tasks | core | 命令执行与后台任务（`shell_exec` 需审批） |
| `subagent` | spawn_subagent | core | 并行子代理编排 |
| `memory` | memorize / search_memory | optional | 个人长期记忆（写入 / 主动召回） |
| `web` | web_fetch / web_search | optional | 联网检索与抓取（默认关闭，SSRF 防护，只读不写工作区；见 [docs/NETWORK_PLUGIN.md](docs/NETWORK_PLUGIN.md)） |
| `evolve` | record_experience / classify_experience / propose_skill / list_experience / list_skills | optional | 自进化：项目经验沉淀 → 用户技能（写入需审批；见 [docs/SELF_EVOLUTION.md](docs/SELF_EVOLUTION.md)） |
| `telemetry` | usage_report / usage_query | optional | 运行时评测：token/费用/时延/工具路径（MeteredRuntime 观察层，不动循环；见 [docs/EVALUATION.md](docs/EVALUATION.md)） |
| `diagnostics` | —（行为插件） | optional | 事件流、故障监控、报告 |
| `mcp` | 动态（`mcp_*`） | optional | 外部 MCP server（stdio）动态装载；`research-agent mcp add/list/remove/test` 管理，写入 `skills/mcp.yml` |

`research-agent plugin list` 查看；插件开关持久化到 `config.yml` 的 `plugins.<id>.enabled`。

---

## 目录结构 (Directory Structure)

```
research-agent/
├── src/research_agent/
│   ├── agent.py            # Host shell（装配 + 回合后横切）
│   ├── runtime.py          # 可替换内核：AgentRuntime / FunctionCallingRuntime + 循环守卫
│   ├── brief.py            # RuntimeBrief（环境/对象/契约，运行时派生）
│   ├── proposal.py         # git 提案：收集 diff + keep/undo
│   ├── checkpoint.py       # 工作区 git 快照 + 回滚（shell 执行前）
│   ├── sandbox.py          # shell_exec 隔离后端（local / docker）
│   ├── context.py          # 令牌感知的分层上下文构建
│   ├── report.py           # 项目经验报告（自进化沉淀载体）
│   ├── evolve.py           # 自进化：判定/生成/校验/记录/使用追踪
│   ├── telemetry.py        # 运行时评测遥测 + MeteredRuntime 观察层
│   ├── cli.py              # CLI: chat / plugin / quality / audit / mcp / evolve / diagnose
│   ├── server.py           # FastAPI 纯 API（SSE 流式）
│   ├── llm.py  config.py   # LLM 抽象 · 配置(API key / data_dir / plugins)
│   ├── guardrail.py  validate.py  trace_log.py   # 治理 / 校验 / 追踪
│   ├── models.py  project_manager.py             # 状态模型 · 会话/工作区存储
│   ├── memory/             # 对话持久化(core) + Tier B 记忆(tier_b/…)
│   ├── diagnostics/        # recorder / monitor / scan / report / audit / feedback
│   ├── quality/            # 质量门禁 collect/gate + HTML 看板
│   └── tools/
│       ├── schema.py       # ToolSchema + ToolPlugin
│       ├── __init__.py     # ToolRegistry（插件 install/uninstall/enable/disable）
│       ├── builtin/        # filesystem · memory · network · evolve · telemetry · 插件声明
│       ├── mcp_loader.py   # MCP 客户端 + 配置 CRUD（动态工具）
│       ├── subagent.py     # 子代理
│       └── git_tool.py     # git checkpoint/rollback
├── skills/                 # 技能定义 + MCP 配置（mcp.yml）
├── my_tools/               # 用户自定义工具（.py）
├── docs/                   # NETWORK_PLUGIN · QUALITY · SELF_EVOLUTION · EVALUATION · PLUGIN_CONTRACT
├── tests/                  # Mock-LLM 确定性测试 + 评测脚本
├── Dockerfile.backend      # 后端容器
├── docker-compose.yml      # 编排（仅 backend）
└── CHANGELOG.md  README.md  pyproject.toml
```

---

## 记忆：对话级 Agentic RAG

写入（回合后异步）：对话 → 小模型蒸馏为 `MemoryUnit`（事实/偏好/决策/踩坑）→ 去重/冲突检测 → SQLite（+ 可选向量）。

读取（Agent 自主）：LLM 判断是否需要记忆 → **从多轮对话中提炼自包含检索词** → 调用 `search_memory` 工具 → **向量优先召回 + MMR 多样性重排**（关键词仅作无向量时后备）→ 接地回答。与文件工作记忆物理隔离。

> 融合策略由评测驱动：实测等权 keyword+vector RRF 因关键词噪声反而劣于纯向量，故采用"向量优先 + MMR（λ=0.7）；关键词仅后备"。

**可复现评测**（`tests/eval_*.py`）：

| 评测 | 结果 |
|------|------|
| 检索器（48 单元 / 27 查询，含 hard-negative） | keyword R@5 62.8% → vector **89.8%** → vector+MMR **91.6%**；MRR 0.56→0.86；aggregate R@5 45%→55% |
| 对话级 agentic（10 场景，含指代消解 / 聚合查询） | 检索决策 **100%**，query 命中 **100%**，接地率 17%（无记忆）→ **100%**（向量召回）；聚合查询覆盖 0%→**100%**（用 `kind` 枚举）；过度检索 0% |

```bash
PYTHONPATH=src python tests/eval_memory_recall.py     # 检索器对比
PYTHONPATH=src python tests/eval_memory_agentic.py    # 48单元/hard-negative：keyword/vector/hybrid + 权重/MMR 扫描
PYTHONPATH=src DEEPSEEK_API_KEY=... python tests/eval_agentic_rag.py   # 对话级 agentic（EVAL_AGENT_VECTOR=1 走向量）
```

---

## 安全边界 (Security Boundaries)

- **Guardrail** — 12 类危险命令正则（`rm -rf /`、`sudo`、`mkfs`、`dd if=`、fork bomb、`curl|bash` …），纯代码、可单测。
- **HITL** — 危险命令触发审批，60s 内未确认自动取消。
- **Path sandbox** — 所有文件操作限定在工作区根，`../` 越权拦截。
- **Container sandbox（可选）** — `shell.backend: auto|local|docker`。`docker` 下命令在一次性容器中运行：`--network none`、仅挂载工作区到 `/work`、`--memory/--cpus/--pids-limit` 限额、`--read-only` rootfs。`docker` 不可用时显式失败，绝不静默降级到无隔离路径。
- **Workspace rollback** — `shell_exec` 执行前用 git 将整个工作树（含未跟踪文件）快照到 `refs/research-agent/checkpoints/*`，污染后可 `restore_checkpoint` 回滚到执行前状态。
- **Parameter validation** — 分发前校验必需参数，静默失败为零。
- **Auto-validation** — 文件写入后自动 `py_compile` / `pytest`，失败回灌模型自纠。
- **API key** — 不硬编码、不入库、不写日志；CI 凭据扫描拦截。

---

## 已知限制 (Known Limitations)

- **Single-user**：无鉴权层，假定本地或可信网络部署。
- **Shell execution**：默认 `backend=auto`；无 docker 时降级为本机 `shell=True`，风险由 guardrail + HITL 缓解。工作区以 bind mount 进容器，故容器只保护工作区**之外**的本机，工作区内写入依赖 checkpoint 回滚。
- **Rollback 边界**：checkpoint 仅支持 git 工作区；git-ignored 文件不进快照、其覆盖不可恢复；`restore_checkpoint` 会删除未跟踪文件，须显式触发。
- **Memory vector layer**：默认关键词检索；语义检索需 `.[vector]` + `RESEARCH_AGENT_MEMORY_VECTOR=1`，缺失时自动降级。
- **评测规模**：记忆评测为自建小规模集，绝对指标偏乐观；以相对提升与方法论为准。
- **提案式改动**：需要工作区是 git 仓库（`git_init` 在项目首次创建时自动执行）；非 git 目录下文件改动按原样直接写入。前端仍为 CLI，提案经 CLI 交互审阅。
- **联网插件**：默认关闭；只支持公开匿名访问，不处理登录态/Cookie；Jina Reader 为第三方降级后端（会看到目标 URL）。
- **MCP**：仅 stdio 传输（无 HTTP/SSE）；配置在 `skills/mcp.yml`（仓库内）；子进程继承宿主环境变量；工具按未知语义（`side_effect=True`）不在 guardrail/HITL 内。
- **运行时评测**：缓存命中率依赖 provider 上报（未上报显示 `n/a`）；回合后蒸馏/压缩归因到上一次 run 的 trace；单次输出不设上限。
- **成本控制**：**不设 token/墙钟硬预算**（有意）；以"检测打转→结果内反馈"为主，`MAX_ROUNDS=50` 为唯一天花板。
- **自进化**：当前只做 skill 沉淀；Agent 自写插件**未实现**，仅给出契约（[docs/PLUGIN_CONTRACT.md](docs/PLUGIN_CONTRACT.md)）。

---

## CI/CD

- **Backend tests**：`pytest`（全 Mock LLM，确定性，无需 API key / 网络）。
- **Docker image**：构建并推送 `pp-backend` 至 GHCR。
- **Credential scanner**：拦截含 API key 模式的提交。

版本历史见 [`CHANGELOG.md`](CHANGELOG.md)。

---

## License

MIT
