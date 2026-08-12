# PaperPilot — Research Coding Agent Harness

PaperPilot is a self-implemented ReAct-style agent loop with 13 tools, governance guardrails, feedback loops, and workspace management. Unlike ChatGPT, it runs tools deterministically on your filesystem with safety boundaries — search papers, read full text, reproduce experiments, write surveys, all in one chat interface.

PaperPilot 是一个自实现的 ReAct 风格 Agent 循环，内置 13 个工具、治理护栏、反馈回路和项目空间管理。与 ChatGPT 不同，它在本地文件系统上确定性执行工具操作，并设有安全边界 —— 搜索论文、阅读全文、复现实验、撰写综述，一站式完成。

---

## 快速开始 (Quick Start)

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m uvicorn research_agent.server:app --host 0.0.0.0 --port 8050
```

Open http://localhost:8050 in your browser. Optionally, build and run the frontend separately:

```bash
cd frontend && npm install && npm run dev
```

Then open http://localhost:5173 and point it at the backend.

---

## API Key 安全配置 (Security Configuration)

**方式一：UI 设置面板 (Recommended)**
Open the web interface → ⚙️ Settings panel → enter your API key. Stored in browser localStorage (Web) or env vars (Desktop).

**方式二：环境变量**
```bash
DEEPSEEK_API_KEY=sk-xxx
```
Supported providers: DeepSeek, OpenAI, Anthropic, OpenAI-compatible.

**方式三：config.yml (⚠ 明文存储风险)**
Write the key into `~/research-agent-data/config.yml`. Plaintext on disk — do not use in shared environments.

**安全红线：**
- **NEVER** hardcode API keys in source code
- **NEVER** commit API keys to git
- **NEVER** log API keys
- CI credential check enforces these rules

---

## 分发方式 (Distribution)

**CLI (命令行):**
```bash
pip install -e .
research-agent chat
```
Or directly: `PYTHONPATH=src python -m research_agent.cli`

**Docker (docker compose):**
```bash
docker build -f Dockerfile.backend -t pp-backend .
docker build -f Dockerfile.frontend -t pp-frontend .
docker compose up
```
Backend on :8050, frontend on :5173 (nginx port 80 in container).

**Desktop App (pywebview):**
```bash
cd frontend && npm run build
PYTHONPATH=src python desktop.py
```
Launches a native window with embedded webview. Requires Edge WebView2 runtime (built-in on Windows 10+).

**WebUI (Render.com):**
Deploy via `render.yaml`, auto-detected. Set `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `LLM_API_KEY` as environment variables in the Render dashboard.
Deployed at: [https://paperpilot.onrender.com](https://paperpilot.onrender.com)

**GitHub Release (可执行文件):**
```bash
pip install pyinstaller
pyinstaller paperpilot.spec
```
Generates `dist/PaperPilot.exe` — a standalone executable bundling backend + frontend. Download from [GitHub Releases](https://github.com/lxcin/research_agent/releases).

---

## 目录结构 (Directory Structure)

```
research-agent/
├── src/research_agent/        # Backend harness
│   ├── agent.py               # Agent loop — ReAct-style main loop (function calling)
│   ├── guardrail.py           # 12-pattern safety guardrail (deterministic, no LLM)
│   ├── server.py              # FastAPI server (SSE streaming)
│   ├── context.py             # Token-aware context builder + skill injection
│   ├── project_manager.py     # File-based project/chat storage (JSON on disk)
│   ├── config.py              # Configuration (API keys, env vars, YAML)
│   ├── llm.py                 # LiteLLM provider wrapper with retry
│   ├── validate.py            # Response validation (hallucination, citation check)
│   ├── retrieval.py           # Hybrid search (vector + BM25 + RRF fusion)
│   ├── ingestion.py           # PDF ingestion + semantic chunking
│   ├── search.py              # arXiv API client
│   ├── store.py               # SQLite storage (metadata, paper cache)
│   ├── knowledge_graph.py     # Paper → claim → relation graph (NetworkX)
│   ├── memory.py              # Conversation turn persistence
│   ├── models.py              # Pydantic data models
│   ├── router.py              # Intent-to-tool-subset routing (dev utility)
│   ├── trace_log.py           # Structured request tracing + logger
│   ├── skill_loader.py        # External skill definitions (YAML/.md)
│   ├── vector_store.py        # ChromaDB vector store wrapper
│   └── tools/                 # Tool registry + 13 built-in tools
│       ├── __init__.py        # ToolRegistry singleton with dedup
│       ├── schema.py          # ToolSchema, ToolResult types
│       ├── validate_params.py # Parameter validation before dispatch
│       ├── subagent.py        # spawn_subagent — parallel subtask execution
│       ├── arxiv_pdf.py       # arXiv PDF fetcher
│       ├── mcp_loader.py      # MCP server loader (stdio/SSE)
│       ├── git_tool.py        # Git checkpoint / rollback integration
│       ├── router.py          # Tool intent routing
│       └── builtin/           # Built-in tool implementations
│           ├── retrieve.py    # retrieve, search_papers, read_paper, update_notes, delete_paper
│           └── filesystem.py  # shell_exec, file_read/write/edit/glob/grep, check_tasks
├── frontend/                  # React + TypeScript + Vite
│   ├── src/
│   │   ├── App.tsx            # Main app (SSE event handling)
│   │   └── components/        # ChatArea, ChatInput, Sidebar, etc.
│   └── dist/                  # Production build output
├── tests/                     # Mock-LLM deterministic tests (pytest)
├── skills/                    # User-defined skill definitions (YAML/.md)
├── my_tools/                  # User-defined custom tools (.py)
├── Dockerfile.backend         # Backend container
├── Dockerfile.frontend        # Frontend container (nginx)
├── docker-compose.yml         # Multi-service orchestration
├── render.yaml                # Render.com deployment config
├── paperpilot.spec            # PyInstaller build spec
├── requirements.txt           # Python dependencies
└── desktop.py                 # pywebview desktop launcher
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
Blocked commands trigger a confirmation dialog in the UI. User has **60 seconds** to approve or reject. Unconfirmed commands are cancelled automatically.

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
- Stored in browser localStorage (Web) or environment variables (Desktop / Docker)
- `.env` excluded from git via `.gitignore`
- CI credential scanner rejects commits containing key patterns

---

## 已知限制 (Known Limitations)

- **ChromaDB / sentence-transformers:** Vector search requires `sentence-transformers`; degrades gracefully to BM25-only if unavailable or on first import. Install with `pip install sentence-transformers` for full hybrid retrieval (vector + BM25 + RRF fusion).
- **Desktop App:** Requires Edge WebView2 runtime — built-in on Windows 10+, optional install on older Windows.
- **Max Rounds:** Maximum 50 agent rounds per request. Configurable via `RESEARCH_AGENT_MAX_ROUNDS` environment variable.
- **Shell Execution:** Uses `shell=True` in `subprocess.run()`. Risk mitigated by the 12-pattern guardrail + HITL confirmation flow.
- **Single-user:** No authentication layer. Assumes local or trusted-network deployment.
- **ArXiv rate limits:** `search_papers` calls the public arXiv API; excessive use may be rate-limited.

---

## CI/CD

### GitHub Actions (configured)
- **Backend tests:** `pytest` on push/PR — all tests use mock LLM (deterministic, no API key needed)
- **Frontend build:** `tsc --noEmit` + `vite build` on push/PR
- **Docker images:** Build and push `pp-backend` and `pp-frontend` to **GitHub Container Registry (GHCR)** on merge to master
- **Credential scanner:** Blocks commits containing API key patterns

### Render Auto-Deploy
Connected via `render.yaml`. Automatically deploys the web service on push to the `master` branch. Set API keys (`DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `LLM_API_KEY`) as environment variables in the Render dashboard.

---

## License

MIT
