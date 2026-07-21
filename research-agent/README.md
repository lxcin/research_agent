# PaperPilot — AI Research Assistant

A research partner agent that remembers your work. Search papers, read full text, reproduce experiments, write surveys — all in one chat interface.

## Quick Start

```bash
# Install
pip install -r requirements.txt
cd frontend && npm install && cd ..

# Run (two terminals)
cd src && uvicorn research_agent.server:app --port 8050
cd frontend && npx vite --port 5173

# Or desktop
python desktop.py
```

## API Key Setup

Open `http://localhost:5173` → ⚙️ Settings → enter your API key.

Supported providers: DeepSeek, OpenAI, Anthropic, OpenAI-compatible.

**Never commit API keys.** They're stored in browser localStorage.

## Deployment

**Docker:**
```bash
docker compose up -d
```

**Render.com:**
[![Deploy](https://render.com/images/deploy-to-render-button.svg)](https://render.com)
1. Fork this repo
2. Connect to Render
3. Set `DEEPSEEK_API_KEY` env var
4. Deploy

## Features

| Feature | Description |
|------|------|
| Paper search | arXiv API + local ChromaDB vector search |
| Literature review | Auto search → read → synthesize → cite |
| Paper reproduction | Read paper → write code → run → compare results |
| Background tasks | Long experiments run in background, check later |
| Multi-project | Sidebar project management, workspace isolation |
| Desktop app | `python desktop.py` for native window |

## Architecture

```
React (Vite) → FastAPI (SSE) → Agent Loop (function calling)
                  ↓
           ToolRegistry (10 tools)
           ├── retrieve, search_papers, read_paper, update_notes
           ├── shell_exec, file_read, file_write, file_edit
           └── file_glob, file_grep, check_tasks
```

## Directory Structure

```
src/research_agent/
  agent.py          # Agent main loop (function calling)
  server.py         # FastAPI server (SSE streaming)
  context.py        # Context builder + skill injection
  tools/            # Tool registry + built-in tools
  skills/           # Skill definitions
  retrieval.py      # Hybrid search (vector + BM25 + RRF)
  ingestion.py      # PDF ingestion + semantic chunking
  search.py         # arXiv API client
  store.py          # SQLite storage
  knowledge_graph.py # Paper → claim → relation graph

frontend/src/
  App.tsx           # Main app (SSE event handling)
  components/       # ChatArea, ChatInput, Sidebar, etc.
```

## Security

- API keys: stored in browser localStorage (Web) or env vars (Desktop)
- No keys in source code, git history, or logs
- `.env` excluded from git via `.gitignore`
- Credential check in CI pipeline

## Known Limitations

- DeepSeek may output `<tool_calls>` XML in some scenarios (model behavior)
- Windows: `sleep` command not available (background tasks use Python)
- Single-user, no auth (local deployment assumed)

## License

MIT