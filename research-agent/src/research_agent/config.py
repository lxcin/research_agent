"""Configuration loader for research-agent."""
import os
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / "research-agent-data"
ENV_DATA_DIR = os.environ.get("RESEARCH_AGENT_DATA_DIR")

def get_data_dir() -> Path:
    path = Path(ENV_DATA_DIR) if ENV_DATA_DIR else DEFAULT_DATA_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path

def load_config() -> dict:
    config_path = get_data_dir() / "config.yml"
    if config_path.exists():
        import yaml
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}

def get_llm_key() -> str | None:
    return os.environ.get("RESEARCH_AGENT_LLM_KEY")