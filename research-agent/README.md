# Research Agent

A persistent research partner agent that remembers your work across sessions.

## Install

```bash
pip install -e .
```

Set your LLM API key:

```bash
export RESEARCH_AGENT_LLM_KEY="your-api-key"
```

## Usage

```bash
# Interactive mode
research-agent chat

# Single query
research-agent chat "What is the attention mechanism?"

# Check status
research-agent status
```

## Data

All data stored locally at `~/research-agent-data/`:
- `chroma_db/` - Vector embeddings
- `research_agent.db` - Papers and projects (SQLite)
- `user_profile.json` - Your research profile
- `agent_self_intro.json` - Agent's self-description

## Skills

Built-in skills triggered by keywords:
- `搜索论文` - Search Semantic Scholar
- `写综述` - Generate literature review
- `写报告` - Generate research report

## Develop

```bash
pip install -e ".[dev]"
pytest tests/ -v
```