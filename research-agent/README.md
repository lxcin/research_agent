# PaperPilot — Personal-Assistant Agent Harness

PaperPilot is a from-scratch AI agent framework (not a LangGraph/Dify wrapper) built around two ideas: **a replaceable agent kernel** and **everything-else-is-a-capability-plugin**. It runs an LLM function-calling loop with deterministic governance, pluggable tools, conversation-level agentic-RAG memory, and developer-facing observability.

PaperPilot 是一个自研的 AI Agent 框架（非 LangGraph/Dify 套壳），围绕两个理念设计：**可替换的 Agent 内核** 与 **一切皆能力插件**。它运行 LLM function-calling 循环，内置确定性治理、可插拔工具、对话级 agentic-RAG 记忆，以及面向开发者的可观测性。

```
Host shell (agent.py)          lifecycle: workspace binding · diagnostics · post-run hooks
        │
        ▼
AgentRuntime (runtime.py)      REPLACEABLE kernel: loop + function calling
        │                        governance/validation injected via RuntimeContext
        ▼
Tool plugins (tools/)          filesystem · shell · subagent · memory · diagnostics · mcp
```

---

## 亮点 (Highlights)

- **可替换内核 (Replaceable kernel)** — `AgentRuntime` host/strategy split: the loop knows no concrete tool; governance, tools and context arrive via `RuntimeContext`. Swap the loop (while-ReAct → other strategies) without touching capabilities.
- **两级工具插件 (Two-level plugins)** — `ToolSchema` (agent-facing granularity: each callable tool) vs `ToolPlugin` (engineering unit: cohesive domain + lifecycle). Runtime install / enable / disable / uninstall with dependency checks and disk-footprint cleanup.
- **纵深治理 (Deterministic governance)** — regex guardrail + path sandbox + HITL approval + post-write `py_compile/pytest` self-correction. All mock-testable, no API key/network.
- **对话级 agentic RAG 记忆 (Conversation-level agentic RAG)** — small-model distillation into typed `MemoryUnit`s; the agent autonomously formulates retrieval queries from dialogue and calls the `search_memory` tool. Hybrid keyword + vector (RRF) recall.
- **可观测性 (Observability)** — every agent event lands in a JSONL event stream; `RunMonitor` detects stalls/repeated-tool/error-streaks; `diagnose` CLI produces reports.

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
| `diagnostics` | —（行为插件） | optional | 事件流、故障监控、报告 |
| `mcp` | 动态 | optional | 从外部 MCP server 动态装载工具 |

`research-agent plugin list` 查看；插件开关持久化到 `config.yml` 的 `plugins.<id>.enabled`。

---

## 目录结构 (Directory Structure)

```
research-agent/
├── src/research_agent/
│   ├── agent.py            # Host shell（装配 + 回合后横切）
│   ├── runtime.py          # 可替换内核：AgentRuntime / FunctionCallingRuntime
│   ├── context.py          # 令牌感知的分层上下文构建
│   ├── cli.py              # CLI: chat / diagnose / plugin
│   ├── server.py           # FastAPI 纯 API（SSE 流式）
│   ├── llm.py  config.py   # LLM 抽象 · 配置(API key / data_dir / plugins)
│   ├── guardrail.py  validate.py  trace_log.py   # 治理 / 校验 / 追踪
│   ├── models.py  project_manager.py             # 状态模型 · 会话/工作区存储
│   ├── memory/             # 对话持久化(core) + Tier B 记忆(tier_b/…)
│   ├── diagnostics/        # recorder / monitor / scan / report / feedback
│   └── tools/
│       ├── schema.py       # ToolSchema + ToolPlugin
│       ├── __init__.py     # ToolRegistry（插件 install/uninstall/enable/disable）
│       ├── builtin/        # filesystem · memory_tool · 插件声明
│       ├── mcp_loader.py   # MCP 客户端（动态工具）
│       ├── subagent.py     # 子代理
│       └── git_tool.py     # git checkpoint/rollback
├── skills/                 # 外部技能定义（YAML 头 + Markdown）
├── my_tools/               # 用户自定义工具（.py）
├── tests/                  # Mock-LLM 确定性测试 + 评测脚本
├── Dockerfile.backend      # 后端容器
├── docker-compose.yml      # 编排（仅 backend）
└── CHANGELOG.md  README.md  pyproject.toml
```

---

## 记忆：对话级 Agentic RAG

写入（回合后异步）：对话 → 小模型蒸馏为 `MemoryUnit`（事实/偏好/决策/踩坑）→ 去重/冲突检测 → SQLite（+ 可选向量）。

读取（Agent 自主）：LLM 判断是否需要记忆 → **从多轮对话中提炼自包含检索词** → 调用 `search_memory` 工具 → 混合召回（jieba 关键词 + `bge-small-zh` 向量 + RRF）→ 接地回答。与文件工作记忆物理隔离。

**可复现评测**（`tests/eval_*.py`）：

| 评测 | 结果 |
|------|------|
| 检索器（48 单元 / 27 查询，含 hard-negative） | 混合召回 **Recall@5 62.8% → 81.7%**，MRR 0.56 → 0.78 |
| 对话级 agentic（8 场景，含指代消解） | 检索决策 **100%**，query 命中 **100%**，接地率 17%（无记忆）→ **83%**（有记忆），过度检索 0% |

```bash
PYTHONPATH=src python tests/eval_memory_recall.py     # 检索器对比
PYTHONPATH=src DEEPSEEK_API_KEY=... python tests/eval_agentic_rag.py   # 对话级 agentic
```

---

## 安全边界 (Security Boundaries)

- **Guardrail** — 12 类危险命令正则（`rm -rf /`、`sudo`、`mkfs`、`dd if=`、fork bomb、`curl|bash` …），纯代码、可单测。
- **HITL** — 危险命令触发审批，60s 内未确认自动取消。
- **Path sandbox** — 所有文件操作限定在工作区根，`../` 越权拦截。
- **Parameter validation** — 分发前校验必需参数，静默失败为零。
- **Auto-validation** — 文件写入后自动 `py_compile` / `pytest`，失败回灌模型自纠。
- **API key** — 不硬编码、不入库、不写日志；CI 凭据扫描拦截。

---

## 已知限制 (Known Limitations)

- **Single-user**：无鉴权层，假定本地或可信网络部署。
- **Shell execution**：`shell=True`，风险由 guardrail + HITL 缓解。
- **Memory vector layer**：默认关键词检索；语义检索需 `.[vector]` + `RESEARCH_AGENT_MEMORY_VECTOR=1`，缺失时自动降级。
- **评测规模**：记忆评测为自建小规模集，绝对指标偏乐观；以相对提升与方法论为准。
- **前端**：V4 起移除 Web/Desktop，交互以 CLI 为主；逐文件提案式（git diff + keep/undo）在规划中。

---

## CI/CD

- **Backend tests**：`pytest`（全 Mock LLM，确定性，无需 API key / 网络）。
- **Docker image**：构建并推送 `pp-backend` 至 GHCR。
- **Credential scanner**：拦截含 API key 模式的提交。

版本历史见 [`CHANGELOG.md`](CHANGELOG.md)。

---

## License

MIT
