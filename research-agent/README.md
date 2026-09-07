# PaperPilot — Research Coding Agent Harness

PaperPilot is a self-implemented ReAct-style agent loop with pluggable tools, governance guardrails, feedback loops, and workspace management. Unlike ChatGPT, it runs tools deterministically on your filesystem with safety boundaries — search papers, read full text, reproduce experiments, write surveys, all in one chat interface. V4 adds two-tier memory (papers as grep-able working memory + personal long-term memory) and developer-facing fault diagnostics.

PaperPilot 是一个自实现的 ReAct 风格 Agent 循环，内置可插拔工具、治理护栏、反馈回路和项目空间管理。与 ChatGPT 不同，它在本地文件系统上确定性执行工具操作，并设有安全边界 —— 搜索论文、阅读全文、复现实验、撰写综述，一站式完成。V4 新增双层记忆（论文作为可 grep 的工作记忆 + 个人长期记忆）与面向开发者的故障诊断。

---

## 快速开始 (Quick Start)

```bash
# 安装
pip install git+https://github.com/lxcin/research_agent.git

# 或本地安装
git clone https://github.com/lxcin/research_agent.git
cd research_agent
pip install -e .

# 启动 CLI（主要交互方式）
research-agent chat

# 启动 API 服务（可选）
PYTHONPATH=src python -m uvicorn research_agent.server:app --host 0.0.0.0 --port 8050
# 开发者诊断
research-agent diagnose
research-agent feature list
```

**依赖**: Python 3.11+

---

## API Key 安全配置 (Security Configuration)

**方式一：环境变量 (Recommended)**
```bash
DEEPSEEK_API_KEY=sk-xxx
```
Supported providers: DeepSeek, OpenAI, Anthropic, OpenAI-compatible.

**方式二：config.yml (⚠ 明文存储风险)**
Write the key into `~/research-agent-data/config.yml`. Plaintext on disk — do not use in shared environments.

**安全红线：**
- **NEVER** hardcode API keys in source code
- **NEVER** commit API keys to git
- **NEVER** log API keys
- CI credential check enforces these rules

---

## 分发方式 (Distribution)

| 形态 | 命令 | 说明 |
|------|------|------|
| **CLI** | `pip install git+https://github.com/lxcin/research_agent.git` → `research-agent chat` | 命令行交互（主要入口） |
| **Docker** | `docker compose up` | 后端 API :8050 |
| **API** | `uvicorn research_agent.server:app` | 纯 API（/api/*、/docs） |
| **源码** | [GitHub Release](https://github.com/lxcin/research_agent/releases) | 下载源码 zip |

> V4 起不再捆绑 Web/Desktop 前端；交互以 CLI 为主，未来重构为逐文件提案式（git diff + keep/undo）。

---

## 目录结构 (Directory Structure)

```
research-agent/
├── src/research_agent/        # Backend harness + API
│   ├── agent.py               # Agent loop — ReAct-style main loop (function calling)
│   ├── context.py             # Token-aware layered context builder
│   ├── server.py              # FastAPI 纯 API（/api/chat SSE 流式等）
│   ├── cli.py                 # CLI: chat / diagnose / feature 子命令
│   ├── llm.py / config.py     # LLM 抽象 + 配置(API key, data_dir, memory)
│   ├── guardrail.py / validate.py / trace_log.py   # 治理/反馈/追踪
│   ├── project_manager.py     # 项目/对话 JSON 存储（workspace 模型）
│   ├── paper_store.py         # 论文 .md 正式区/临时区两阶段布局
│   ├── retrieval.py           # grep_papers 关键词检索（jieba）
│   ├── search.py              # arXiv API 客户端
│   ├── store.py               # SQLite（历史论文元数据/项目关联）
│   ├── memory/                # 对话持久化(core) + Tier B 个人记忆(tier_b/…)
│   ├── diagnostics/           # 事件流/监控/scan/report/feedback
│   ├── features/              # 可插拔 Feature 注册表(list/enable/disable/uninstall)
│   ├── knowledge_graph.py / ingestion.py / vector_store.py  # 历史 API(可选 feature)
│   └── tools/                 # ToolRegistry + builtin/subagent/arxiv_pdf/mcp_loader/git_tool
├── skills/                    # 外部技能定义（YAML 头 + Markdown）
├── my_tools/                  # 用户自定义工具（.py）
├── tests/                     # Mock-LLM 确定性测试（pytest）
├── Dockerfile.backend         # 后端容器
├── docker-compose.yml         # 编排（仅 backend）
├── render.yaml                # Render.com 部署
└── requirements.txt / pyproject.toml
```

---

## 安全边界 (Security Boundaries)

### Guardrail — 12-Pattern Deterministic Blocker
All checks are code-only, no LLM involved. Each pattern is testable with mock input. Defined in `src/research_agent/guardrail.py:9`.

| Pattern | Blocks |
|---------|--------|
| `rm -rf /` / `~` / `$HOME` | Recursive root/home deletion |
| `mkfs.` | Filesystem formatting |
| `dd if=` | Raw disk write |
| `> /dev/sd*` | Block device overwrite |
| `chmod 777 /` | World-writable root |
| `:(){` (fork bomb) | Denial-of-service |
| `wget \| sh` / `curl \| bash` | Pipe-to-shell |
| `eval` | Suspicious eval |
| `sudo` | Privilege escalation |

### HITL — Human-in-the-Loop Approval
Blocked commands trigger a confirmation request. User has **60 seconds** to approve or reject. Unconfirmed commands are cancelled automatically.

### Path Sandbox
All file operations (`file_read`, `file_write`, `file_edit`, `file_glob`, `file_grep`) are scoped to the active workspace directory. Path traversal (`../`) is resolved via `os.path.normpath` and checked against the workspace root. Any path escaping the workspace is blocked before dispatch.

### Parameter Validation
Before dispatching any tool, `validate_tool_params` (`src/research_agent/tools/validate_params.py`) checks that all required parameters are present and correctly typed. Invalid calls are returned as errors with explanation — no silent failures.

### Auto-Validation
After every `file_write` or `file_edit` on `.py` or `.java` files, the agent automatically runs a syntax check:
- `.py` → `py_compile.compile()` (+ `pytest` if test file)
- `.java` → `javac` compile check

Validation failures are injected as system messages so the LLM can self-correct in the next round.

### API Key Protection
- Never hardcoded in source code
- Set via environment variables (CLI / Docker) or `config.yml`
- `.env` excluded from git via `.gitignore`
- CI credential scanner rejects commits containing key patterns

---

## 已知限制 (Known Limitations)

- **记忆向量层（可选）:** Tier B 个人记忆默认用关键词检索（始终可用）。如需语义检索，安装 `sentence-transformers` 并设 `RESEARCH_AGENT_MEMORY_VECTOR=1`；模型缺失时自动降级回关键词，不影响主功能。
- **Max Rounds:** 单个请求最多 50 轮 agent 循环。可用 `RESEARCH_AGENT_MAX_ROUNDS` 环境变量配置。
- **Shell Execution:** 使用 `shell=True`。风险由 12 模式 guardrail + HITL 审批流程缓解。
- **Single-user:** 无鉴权层。假定本地或可信网络部署。
- **ArXiv rate limits:** `search_papers` 调用公开 arXiv API；过度使用可能被限流。
- **前端（重构中）:** V4 已移除 Web/Desktop 前端；交互以 CLI 为主，逐文件提案式（git diff + keep/undo）交互在规划中。

---

## CI/CD

### GitHub Actions (configured)
- **Backend tests:** `pytest` on push/PR — all tests use mock LLM (deterministic, no API key needed)
- **Docker image:** Build and push `pp-backend` to **GitHub Container Registry (GHCR)** on merge to master
- **Credential scanner:** Blocks commits containing API key patterns

### Render Auto-Deploy
Connected via `render.yaml`. Automatically deploys the web service on push to the `master` branch. Set API keys (`DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `LLM_API_KEY`) as environment variables in the Render dashboard.

---

## License

MIT
