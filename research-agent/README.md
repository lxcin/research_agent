# PaperPilot — Personal-Assistant Agent Harness

PaperPilot is a from-scratch AI agent framework (not a LangGraph/Dify wrapper) built around two ideas: **a replaceable agent kernel** and **everything-else-is-a-capability-plugin**. It runs an LLM function-calling loop with deterministic governance, pluggable tools, episodic task memory (plan-as-memory), and developer-facing observability.

PaperPilot 是一个自研的 AI Agent 框架（非 LangGraph/Dify 套壳），围绕两个理念设计：**可替换的 Agent 内核** 与 **一切皆能力插件**。它运行 LLM function-calling 循环，内置确定性治理、可插拔工具、任务情景记忆（plan-as-memory），以及面向开发者的可观测性。

> **架构基线（2026-10-16）** — 稳定可替换 Kernel · Runtime 宿主 · 插件生命周期与注入状态 · 中断恢复 · 版本化文件编辑 · 分层评测。包版本仍为 v4.3.0。

```text
Application assembly (agent.py)      会话 / 工作区绑定、插件装配、请求生命周期
             │
             ▼
AgentKernel (kernel.py)              如何思考：循环、推进、结束条件
             │ KernelHost 契约
             ▼
RuntimeHost (runtime.py)             如何交互：上下文、模型、工具、重试、取消、预算与检查点
             │
             ▼
Capability plugins (tools/)          工具契约、状态字段、依赖、资源归属与卸载排空
```

---

## 亮点 (Highlights)

- **可替换内核 (Replaceable kernel)** — `AgentKernel` 只依赖 `KernelHost`，不认识具体工具；`RuntimeHost` 提供模型和工具交互。工厂在启动或显式切换策略时安装稳定实例，不逐回合创建；请求状态留在调用内。
- **两级工具插件 (Two-level plugins)** — `ToolSchema` (agent-facing granularity) vs `ToolPlugin` (engineering unit: cohesive domain + lifecycle). Runtime install / enable / disable / uninstall with dependency checks and disk-footprint cleanup. 插件自声明工具、状态与评测能力，数量以 `research-agent plugin list` 为准。
- **纵深治理 + 循环守卫 (Governance + loop breaker)** — regex guardrail + path sandbox + HITL approval + post-write `py_compile/pytest` self-correction; a deterministic `_ProgressGuard` feeds repeated-call / error-streak feedback back **inside the tool result** (no extra messages, cache-friendly). All mock-testable, no API key/network.
- **任务计划 · 情景记忆 (Plan-as-memory)** — 多步任务由 Agent 先 `plan_update` 登记**结构化步骤**（goal + steps + `depends_on` + `reason` + `decisions`），再 `plan_execute` **按 DAG 依赖调度：无依赖步骤并行派发子代理**，失败步骤及下游自动 `blocked`。回合结束**异步写入任务情景记忆**：有显式计划则存之，否则由小模型把"目标/决策(含原因)/步骤/结果"提炼成 episode（`plan.distill` 关则纯启发式），全程不阻塞请求线程。召回**向量优先**（缺则 jieba 关键词回退），由 Agent 自己提炼检索词调 `plan_search` 找回历史同类任务的做法/决策/踩坑。计划存项目数据目录，不进 git 工作区。
- **联网能力插件 (Web plugin)** — `web_fetch` / `web_search` behind an **SSRF-aware URL policy** (scheme allowlist, IP-range guard incl. IPv4-mapped/6to4/NAT64 unwrap, per-hop redirect re-validation, size caps); multi-backend fetch (direct → Jina Reader) and search (Exa/Tavily/Serper/DDG routing) + `web-doctor`. Opt-in, read-only.
- **自进化 (Self-evolution)** — distil project experience into **versioned, toggleable user skills** (build → static review → human approval → provenance records), with usage tracking and utility A/B. Follows a build-vs-buy ladder (reuse/MCP first); plugin authoring deferred.
- **运行时评测 + 质量门禁 (Runtime eval + quality gate)** — `telemetry` (an **AgentRuntime observation layer**, loop untouched) records tokens / **cache-aware cost** / latency / tool paths; `audit` scores framework effectiveness; `quality` gates build+coverage+security and renders an HTML scoreboard, wired into CI.
- **运行时简报 (Runtime brief)** — a runtime-derived environment/principal/contract brief injected before work (correct across OSes, not hardcoded), to cut wasted environment-guessing rounds.
- **MCP 外部工具** — import external MCP servers via `research-agent mcp add/list/remove/test` (stdio; tools auto-register as `mcp_*`).
- **提案式文件改动 (Git-based change proposals)** — agent file edits are staged in the workspace git repo (not auto-committed); after each turn they are shown as a diff and the user decides `keep` (commit) or `undo` (restore/delete), per file or all at once — opencode / Claude Code style.
- **轮内沙箱 + 工作区回滚 (Run-scoped sandbox + rollback)** — `shell_exec` lazily creates one Docker container per run and reuses it via `docker exec` (network off, limited mounts, CPU/mem/pid caps). Containers are removed at run end, including error and cancellation paths; cross-run reuse is retired. Explicit background commands own separate one-shot containers until completion. Git snapshots retain the latest **turn** (`shell.checkpoint: turn|off`), with reviewed selective undo and reverse three-way text merging.

---

## 快速开始 (Quick Start)

```bash
git clone https://github.com/lxcin/research_agent.git
cd research_agent/research-agent
pip install -e .

# 配置 API Key
export DEEPSEEK_API_KEY=sk-xxx      # 或写入 ~/research-agent-data/config.yml

# 交互式 CLI（主要入口）
research-agent chat

# 能力插件管理
research-agent plugin list
research-agent plugin disable web

# 开发者诊断
research-agent diagnose

# 内部质量门禁（测试/覆盖率/安全 → PASS/FAIL + HTML 看板）
research-agent quality --open

# 运行时全链路审计：追踪→审查→评分（框架有效性）+ 报告
research-agent audit --report

# 运行时评测遥测：token/费用/时延/工具路径（telemetry 插件默认启用）
research-agent plugin list            # 查看 telemetry
# Agent 侧：usage_report / usage_query 工具

# 任务正确性评测：列用例不会调用模型；run 会显式调用配置模型
research-agent evaluation list
research-agent evaluation run --strategy function_calling --repeats 3 --output reports/tasks
# 同任务策略对照、自定义循环与插件用例声明见 docs/TASK_EVALUATION.md

# 多窗口隔离试用：每会话独立分支与 worktree（源仓库需已有提交且业务文件清洁）
research-agent chat --workspace <Git仓库根目录> --isolate-session
# 会话恢复、显式合入及 Docker 边界见 docs/SESSION_WORKTREES.md
# 合入后安全归档/恢复、空间统计、容器上限与只读共享挂载见 docs/STORAGE_POLICY.md
# 容器崩溃回收、宿主 Git 查询和真实验收见 docs/DOCKER_OWNERSHIP_AND_ACCEPTANCE.md
# 后台任务查询/取消、会话关闭及退出排空见 docs/BACKGROUND_TASKS.md
# 工作区创建中断恢复、审阅式文件/暂存区回退见 docs/WORKSPACE_RECOVERY_AND_ROLLBACK.md

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

各部分职责与阅读顺序见 [当前模块导览](docs/MODULE_GUIDE.md)。

| 层 | 文件 | 职责 |
|----|------|------|
| **Host 外壳** | `agent.py` | workspace/project 绑定、插件装载、诊断包装、回合后横切（持久化/压缩/情景记忆提炼） |
| **Kernel 内核** | `kernel.py` / `kernel_factory.py` | 可替换思考循环、通用结束条件；工厂安装并复用稳定内核实例 |
| **Runtime 宿主** | `runtime.py` / `model_adapter.py` | 模型和工具交互、重试、取消、上下文预算、执行检查点 |
| **注入状态与资源** | `state_components.py` / `plugins.py` | 字段归属、生命周期、持久化契约、依赖管理与卸载排空 |
| **Capabilities 插件** | `tools/` | `ToolSchema`（Agent 粒度）+ `ToolPlugin`（工程粒度，可装卸） |
| **Conversation 对话** | `memory/` | 对话/工作区持久化（core）；任务情景记忆见 `plan.py` |
| **Diagnostics 诊断** | `diagnostics/` | 事件流 / 监控 / 扫描 / 报告 |

**内核契约**：`AgentKernel.run(host, request)` 通过 `KernelHost` 的 prepare/select/execute/answer 等端口执行。`RuntimeContext` 是宿主的装配参数，包含模型、state、registry、emit 及治理钩子；这些不直接成为内核依赖。

`AgentState` 是注入字段的组织容器，不等同于任务本身。插件声明字段类型、owner、lifetime 和编解码；会话/执行数据按契约保存，容器、线程、连接等活资源不序列化。详见 [基础契约](docs/FOUNDATION.md)、[内核](docs/KERNEL.md)与[工具和状态](docs/TOOL_STATE_CONTRACTS.md)。

---

## 文件读写与上下文

文件真实状态以磁盘为准。正常读取在一次流式扫描中得到正文与 SHA-256，按会话记录版本和实际已读范围；按行分页，超长行用字符列续读，最大文件为 64 MiB，支持严格 UTF-8 / UTF-8 BOM。

- 编辑必须携带 `expected_version`，先比对真实内容哈希；`old_string` 必须唯一且位于已读范围内，写入前再复检版本。新建声明 `expected_version="absent"`，整文件覆盖要求完整读取覆盖。
- `FILE_CHANGED` 表示文件变化、旧行号可能偏移；`NO_MATCH` 表示原文不存在；`MULTI_MATCH` 表示多处或重叠匹配。均拒绝写入，指导 Agent 重新定位并读取相关范围，不默认重读整个文件。
- Harness 用元数据提示过期，不自动恢复或注入源码；Agent 主动读取。轮内按上下文水位及最小节省量批量折叠，完整工具证据独立保存。
- 编辑回执给出完成状态、前后版本、修改范围、diff 和读回校验；编辑完成不等于测试通过。

不引入文件索引、哈希缓存或片段级放行，也不新增缓存存储。原有任务恢复和工具证据继续落盘。元数据可能漏报保留时间戳的改写，编辑时的内容哈希负责兜底；普通文件写入仍不是事务。

详见 [文件版本与编辑回执](docs/TOOL_CONTEXT_AND_EDIT_RECEIPTS.md)、[上下文预算](docs/CONTEXT_BUDGET.md)。

## 执行恢复与评测

网络中断保存最后完整步骤，恢复后继续执行，已完成工具不盲目重放。已确认失败的工具网络调用返回失败观察；副作用结果未知时停止并要求核实，不自动重复执行。取消、后台任务和插件停用遵守资源排空边界。

评测分为宿主机制、插件契约和任务结果：既检查事件、资源与工具健康，也用工作区产物和参考答案判断任务是否真正完成。插件可声明检查项和任务用例，支持相同任务下不同 Kernel 的策略对照。

```bash
pip install -e ".[dev]"
python -m pytest tests -q
# 真实 Docker 验证需本机 Docker 可用；真实模型评测会调用配置模型。
```

本地测试报告在 `artifacts/` 生成，不作为源码提交。恢复、取消和评测细节见 [网络恢复](docs/NETWORK_RECOVERY.md)、[取消与排空](docs/CANCELLATION_AND_DRAIN.md)、[任务评测](docs/TASK_EVALUATION.md)。

---

## 能力插件 (Capability Plugins)

| 插件 | 工具 | 类型 | 说明 |
|------|------|------|------|
| `filesystem` | file_read / file_write / file_edit / file_glob / file_grep / git_inspect / context_result_read | core | 工作区文件、版本化编辑、宿主 Git 查询、历史结果回溯 |
| `shell` | shell_exec / check_tasks / cancel_task | core | 命令执行与后台任务（`shell_exec` 需审批） |
| `subagent` | spawn_subagent | core | 并行子代理编排 |
| `web` | web_fetch / web_search | optional | 联网检索与抓取（默认关闭，SSRF 防护，只读不写工作区；见 [docs/NETWORK_PLUGIN.md](docs/NETWORK_PLUGIN.md)） |
| `evolve` | record_experience / classify_experience / propose_skill / list_experience / list_skills | optional | 自进化：项目经验沉淀 → 用户技能（写入需审批；见 [docs/SELF_EVOLUTION.md](docs/SELF_EVOLUTION.md)） |
| `telemetry` | usage_report / usage_query | optional | 运行时评测：token/费用/时延/工具路径（MeteredRuntime 观察层，不动循环；见 [docs/EVALUATION.md](docs/EVALUATION.md)） |
| `plan` | plan_update / plan_search / plan_execute | optional | 任务计划·情景记忆：登记步骤（含 reason/decisions）→ DAG 调度执行（无依赖并行派发子代理）→ 回合末自动写入情景记忆 → Agent 提炼检索词、向量优先召回历史同类任务；存项目数据目录（不进 git 工作区） |
| `diagnostics` | —（行为插件） | optional | 事件流、故障监控、报告 |
| `mcp` | 动态（`mcp_*`） | optional | 外部 MCP server（stdio）动态装载；`research-agent mcp add/list/remove/test` 管理，写入 `skills/mcp.yml` |

`research-agent plugin list` 查看；插件开关持久化到 `config.yml` 的 `plugins.<id>.enabled`。

---

## 目录结构 (Directory Structure)

```
research-agent/
├── src/research_agent/
│   ├── agent.py            # Host shell（装配 + 回合后横切）
│   ├── kernel.py  kernel_factory.py  # 思考循环与稳定内核工厂
│   ├── runtime.py          # RuntimeHost：模型、工具、预算与恢复交互
│   ├── state_components.py  plugins.py  # 注入状态与资源生命周期
│   ├── file_views.py  tool_context.py  # 文件版本、已读范围与批量折叠
│   ├── brief.py            # RuntimeBrief（环境/对象/契约，运行时派生）
│   ├── proposal.py         # git 提案：收集 diff + keep/undo
│   ├── checkpoint.py  rollback.py  # 本轮快照、审阅及文件三方撤销
│   ├── execution.py  network_recovery.py  # 工具完成记录与中断恢复
│   ├── sandbox.py          # shell_exec 隔离后端（local / docker）
│   ├── context.py          # 令牌感知的分层上下文构建
│   ├── report.py           # 项目经验报告（自进化沉淀载体）
│   ├── evolve.py           # 自进化：判定/生成/校验/记录/使用追踪
│   ├── telemetry.py        # 运行时评测遥测 + MeteredRuntime 观察层
│   ├── cli.py              # CLI: chat / plugin / evaluation / quality / audit / mcp / evolve / diagnose
│   ├── server.py           # FastAPI 纯 API（SSE 流式）
│   ├── llm.py  config.py   # LLM 抽象 · 配置(API key / data_dir / plugins)
│   ├── guardrail.py  validate.py  trace_log.py   # 治理 / 校验 / 追踪
│   ├── models.py  project_manager.py             # 状态模型 · 会话/工作区存储
│   ├── memory/             # 对话/工作区持久化(core)
│   ├── plan.py  plan_pipeline.py  plan_executor.py  embedding.py   # 任务情景记忆
│   ├── diagnostics/        # recorder / monitor / scan / report / audit
│   ├── quality/            # 质量门禁 collect/gate + HTML 看板
│   └── tools/
│       ├── schema.py       # ToolSchema + ToolPlugin
│       ├── __init__.py     # ToolRegistry（插件 install/uninstall/enable/disable）
│       ├── builtin/        # filesystem · plan · network · evolve · telemetry · 插件声明
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

## 任务情景记忆 (Episodic task memory)

面向助手：自动写入的不是"用户画像"，而是**任务情景**。

写入（回合后异步，不阻塞请求线程）：有显式计划则存该计划；否则小模型把本回合归纳为 episode（`goal / decisions(含原因) / steps / outcome`，只喂对话与工具**名**，过滤工具输出），失败回退启发式。落到**本地 SQLite（`data_dir/episodes.db`，一文件存所有项目；embedding 存为 float32 BLOB）**，不进 git 工作区。

读取（Agent 自主，**跨项目**）：Agent 自己提炼检索词调用 `plan_search` → 默认召回**所有项目**的历史同类任务（同项目仅作 tie-break 加权；`scope=project` 可只看当前项目）→ **向量优先**（`numpy` 矩阵乘余弦；需 `.[vector]` + `RESEARCH_AGENT_EMBEDDING=1`），缺失时回退 **jieba 关键词**。**无向量库、无服务端**；episode 到万量级无压力。计划本身也可由 `plan_execute` 按 DAG 依赖自动调度执行。

```bash
research-agent plugin list            # plan 插件：plan_update / plan_search / plan_execute
# 记忆写入异步：run 末自动落 episode；测试用 plan_pipeline.drain()
```

> Tier B 语义记忆（`MemoryUnit` / `search_memory` / `memorize`）已随本设计移除；自动记忆写入统一为任务情景记忆。

---

## 安全边界 (Security Boundaries)

- **Guardrail** — 12 类危险命令正则（`rm -rf /`、`sudo`、`mkfs`、`dd if=`、fork bomb、`curl|bash` …），纯代码、可单测。
- **HITL** — 危险命令触发审批，60s 内未确认自动取消。
- **Path sandbox** — 所有文件操作限定在工作区根，`../` 越权拦截。
- **Container sandbox（可选）** — `shell.backend: auto|local|docker` 选择隔离来源；容器统一按单轮任务管理（`shell.session: ephemeral`）。首次 Shell 执行时创建，轮内通过 `docker exec` 复用，结束、异常或取消后回收，不跨轮常驻；普通文件编辑不创建容器。工作区挂载到 `/work`，默认禁网、只读根文件系统并限制 CPU、内存和进程数；显式共享数据仅只读挂载。后台任务使用独立一次性容器，任务结束回收，共用容量上限。显式 `docker` 后端不可用时失败。
- **Workspace rollback** — `shell.checkpoint: turn|off`，旧 `command` 配置按 `turn` 处理。每轮保存执行前/结束原始内容及暂存区，仅保留最近一轮；新轮基线成功建立后释放旧引用，冲突和部分恢复则继续保留。普通文件工具也覆盖。支持强制审阅的严格整轮恢复，以及保留后续不冲突人工编辑的按文件三方合并撤销，见[实现与入口](docs/SELECTIVE_UNDO_PLAN.md)。
- **Parameter validation** — 分发前校验必需参数，静默失败为零。
- **Auto-validation** — 文件写入后自动 `py_compile` / `pytest`，失败回灌模型自纠。
- **API key** — 不硬编码、不入库、不写日志；CI 凭据扫描拦截。

---

## 已知限制 (Known Limitations)

- **Single-user**：无鉴权层，假定本地或可信网络部署。
- **Shell execution**：默认 `backend=auto`；无 docker 时降级为本机 `shell=True`，风险由 guardrail + HITL 缓解。工作区以 bind mount 进容器，故容器只保护工作区**之外**的本机，工作区内写入依赖 checkpoint 回滚。
- **Rollback 边界**：仅支持 Git 工作区最近一轮，不支持任意历史或按 hunk 撤销；人工后续暂存、同位置冲突及未知副作用不自动解决。严格 restore 拒绝后续编辑，选择性 undo 生成三方候选结果供重新审阅；只删除明确清单中的 Agent 新增文件。Git 忽略文件与远程副作用不在恢复范围。释放旧引用后异步执行保留宽限期的 Git 回收，可用 `research-agent git-maintenance status` 查询，不保证立即释放磁盘。
- **Embedding layer（可选）**：episode 向量召回默认关闭；语义召回需 `.[vector]` + `RESEARCH_AGENT_EMBEDDING=1`（兼容旧名 `RESEARCH_AGENT_MEMORY_VECTOR`），缺失时自动降级为关键词。
- **情景记忆**：自动写入为启发式或小模型提炼，不保证任务边界判定完美；无计划的回合按"工具活动"触发。
- **提案式改动**：需要工作区是 git 仓库（`git_init` 在项目首次创建时自动执行）；非 git 目录下文件改动按原样直接写入。前端仍为 CLI，提案经 CLI 交互审阅。
- **联网插件**：默认关闭；只支持公开匿名访问，不处理登录态/Cookie；Jina Reader 为第三方降级后端（会看到目标 URL）。
- **MCP**：仅 stdio 传输（无 HTTP/SSE）；配置在 `skills/mcp.yml`（仓库内）；子进程继承宿主环境变量；工具按未知语义（`side_effect=True`）不在 guardrail/HITL 内。
- **运行时评测**：缓存命中率和 token 费用依赖 provider 的上报及价格配置；缺失时显示未知，不用零值代替。脚本化模型测试证明调用链与约束，不能替代真实模型任务评测。
- **成本控制**：宿主约束上下文窗口、输出预留和安全余量，内核限制轮次并反馈重复调用/连续失败；不承诺精确的整任务费用或墙钟上限。
- **自进化**：当前只做 skill 沉淀；Agent 自写插件**未实现**，仅给出契约（[docs/PLUGIN_CONTRACT.md](docs/PLUGIN_CONTRACT.md)）。

---

## CI/CD

- **Backend tests**：`pytest`（全 Mock LLM，确定性，无需 API key / 网络）。
- **Docker image**：构建并推送 `pp-backend` 至 GHCR。
- **Credential scanner**：现有 CI 检测源码中的 API key 模式并告警。

版本历史见 [`CHANGELOG.md`](CHANGELOG.md)。

---

## License

MIT
