"""Configuration loader for research-agent."""
import os
import yaml
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / "research-agent-data"


def get_data_dir() -> Path:
    env_dir = os.environ.get("RESEARCH_AGENT_DATA_DIR")
    path = Path(env_dir) if env_dir else DEFAULT_DATA_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_config_path() -> Path:
    return get_data_dir() / "config.yml"


def load_config() -> dict:
    config_path = get_config_path()
    if not config_path.exists():
        _write_default_config(config_path)
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_default_config(path: Path):
    path.write_text("""# Research Agent Configuration
model:
  provider: anthropic       # anthropic / openai / deepseek / openai_compatible
  name: claude-3-haiku-20240307
  api_key_env: ANTHROPIC_API_KEY
  # For custom endpoints (openai_compatible):
  # api_base: https://your-proxy.com/v1

embedding:
  model: BAAI/bge-m3
  # Use local_files_only if offline
  local_files_only: false

context:
  max_tokens: 4000
  compress_threshold: 10

projects:
  data_dir: ~/research-agent-data
""", encoding="utf-8")


def get_model_config() -> dict:
    config = load_config()
    return config.get("model", {
        "provider": "anthropic",
        "name": "claude-3-haiku-20240307",
        "api_key_env": "ANTHROPIC_API_KEY",
    })


def get_model_name() -> str:
    return get_model_config().get("name", "claude-3-haiku-20240307")


def get_api_key() -> str | None:
    key_env = get_model_config().get("api_key_env", "ANTHROPIC_API_KEY")
    return os.environ.get(key_env)


def get_api_base() -> str | None:
    return get_model_config().get("api_base")


def get_llm_key() -> str | None:
    return os.environ.get("RESEARCH_AGENT_LLM_KEY")


def get_max_context_tokens(model_name: str = "") -> int:
    env = os.environ.get("RESEARCH_AGENT_MAX_CONTEXT_TOKENS")
    if env:
        return int(env)
    name = model_name.lower()
    if "deepseek" in name: return 64000
    if "gpt-4o" in name: return 128000
    if "gpt-4" in name: return 128000
    if "claude" in name: return 200000
    if "gemini" in name: return 1000000
    if "llama" in name: return 128000
    if "qwen" in name: return 128000
    return 128000


def get_temperature(default: float = 0.7) -> float:
    return float(os.environ.get("RESEARCH_AGENT_TEMPERATURE", str(default)))


def get_max_output_tokens() -> int | None:
    env = os.environ.get("RESEARCH_AGENT_MAX_OUTPUT_TOKENS")
    if env:
        return int(env)
    return None  # None = no limit, model decides