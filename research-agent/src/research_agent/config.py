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
  compress_enabled: true   # rolling summarization of old turns
  compress_ratio: 0.6      # trigger when history >= ratio * model context window

memory:
  enabled: true            # Tier B personal memory write/read
  distill: true            # post-turn async distillation (LLM cost) — turn off to save cost
  max_inject_tokens: 1500  # read path context budget (Phase C)
  max_retrievals: 4        # per-turn retrieval cap (loop guard)
  candidate_pool: 40       # stage-1 recall pool before MMR re-rank
  confidence_strong: 0.60  # top-1 cosine >= this => confident hit (calibrated, bge-zh)
  confidence_weak: 0.53    # >= this => weak (retry once); below => likely absent
  # vector: set env RESEARCH_AGENT_MEMORY_VECTOR=1 to enable embedding layer

shell:
  backend: auto            # auto / local / docker  (docker = isolated sandbox)
  image: python:3.11-slim  # container image used when backend resolves to docker
  network: false           # false = no network access inside the sandbox
  memory: 512m             # container memory cap
  cpus: "1.0"              # container CPU cap
  pids_limit: 256          # container process cap (fork-bomb guard)
  checkpoint: true         # snapshot workspace (git) before shell_exec for rollback

web:
  # 联网插件（默认关闭；research-agent plugin enable web）
  timeout: 15              # 单次请求超时秒数
  max_bytes: 2000000       # 普通响应体读取上限（超出即截断）
  max_pdf_bytes: 20000000  # PDF 响应体读取上限（PDF 通常更大）
  pdf_max_pages: 50        # PDF 最多解析页数
  max_chars: 8000          # 返回给模型的正文上限
  max_redirects: 5         # 手动跟随的最大跳数（每跳都重新做安全检查）
  fetch_backends: [direct, jina]  # 有序抓取后端：direct=本机直连；jina=r.jina.ai（读 JS 重页面）
  jina_reader_base: https://r.jina.ai/
  fetch_min_chars: 200     # direct 提取正文短于此值时降级到下一个抓取后端
  allow_private: false     # true = 允许访问内网/本机（会关闭 SSRF 防护，危险）
  allow_localhost: false   # true = 仅放行回环(localhost/127.0.0.1/::1)直连本地 dev 服务
  allow_domains: []        # 非空 = 仅允许这些域名及其子域
  deny_domains: []         # 始终拒绝这些域名及其子域
  search_provider: none    # 单后端（兼容旧配置）none / exa / tavily / serper / duckduckgo
  search_providers: []     # 有序多后端（借鉴 Agent-Reach）：按序探测，第一个可用即当选
                           # 例: [exa, tavily, duckduckgo]
  search_api_key_env: TAVILY_API_KEY
  exa_api_key_env: EXA_API_KEY
  search_max_results: 5

projects:
  data_dir: ~/research-agent-data
""", encoding="utf-8")


def get_memory_config() -> dict:
    config = load_config()
    mem = config.get("memory", {})
    def _f(key, default):
        try:
            return float(mem.get(key, default))
        except (TypeError, ValueError):
            return default
    return {
        "enabled": bool(mem.get("enabled", True)),
        "distill": bool(mem.get("distill", True)),
        "max_inject_tokens": int(mem.get("max_inject_tokens", 1500)),
        "max_retrievals": int(mem.get("max_retrievals", 4)),
        "candidate_pool": int(mem.get("candidate_pool", 40)),
        "confidence_strong": _f("confidence_strong", 0.60),
        "confidence_weak": _f("confidence_weak", 0.53),
    }


def get_context_config() -> dict:
    """Context/history management settings (compression trigger, ratio, ...)."""
    config = load_config()
    ctx = config.get("context", {})
    try:
        ratio = float(ctx.get("compress_ratio", 0.6))
    except (TypeError, ValueError):
        ratio = 0.6
    try:
        max_tokens = int(ctx.get("max_tokens", 4000))
    except (TypeError, ValueError):
        max_tokens = 4000
    return {
        "max_tokens": max_tokens,
        "compress_threshold": int(ctx.get("compress_threshold", 10)),
        "compress_enabled": bool(ctx.get("compress_enabled", True)),
        "compress_ratio": ratio,
    }


def get_web_config() -> dict:
    """Network plugin settings (research_agent.tools.builtin.network).

    Defaults are safe: SSRF guard on, no domain restriction, no search provider.
    """
    config = load_config()
    web = config.get("web", {})
    if not isinstance(web, dict):
        web = {}

    def _int(key, default):
        try:
            return int(web.get(key, default))
        except (TypeError, ValueError):
            return default

    def _list(key):
        val = web.get(key, [])
        return [str(x).strip() for x in val if str(x).strip()] if isinstance(val, list) else []

    return {
        "timeout": _int("timeout", 15),
        "max_bytes": _int("max_bytes", 2_000_000),
        "max_pdf_bytes": _int("max_pdf_bytes", 20_000_000),
        "pdf_max_pages": _int("pdf_max_pages", 50),
        "max_chars": _int("max_chars", 8000),
        "max_redirects": _int("max_redirects", 5),
        "fetch_backends": _list("fetch_backends") or ["direct", "jina"],
        "jina_reader_base": str(web.get("jina_reader_base", "https://r.jina.ai/")),
        "fetch_min_chars": _int("fetch_min_chars", 200),
        "allow_private": bool(web.get("allow_private", False)),
        "allow_localhost": bool(web.get("allow_localhost", False)),
        "allow_domains": _list("allow_domains"),
        "deny_domains": _list("deny_domains"),
        "search_provider": str(web.get("search_provider", "none") or "none").lower(),
        "search_providers": _list("search_providers"),
        "search_api_key_env": str(web.get("search_api_key_env", "TAVILY_API_KEY")),
        "exa_api_key_env": str(web.get("exa_api_key_env", "EXA_API_KEY")),
        "search_max_results": _int("search_max_results", 5),
        "user_agent": str(web.get("user_agent", "PaperPilot/1.0")),
    }


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
    # 1. Check env var (highest priority)
    key = os.environ.get(key_env)
    if key:
        return key
    # 2. Check config file (written by setup or frontend)
    config_key = get_model_config().get("api_key", "")
    if config_key and config_key != "__MISSING__":
        return config_key
    # 3. Generic fallback
    return os.environ.get("RESEARCH_AGENT_LLM_KEY")


def get_api_base() -> str | None:
    return get_model_config().get("api_base")




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