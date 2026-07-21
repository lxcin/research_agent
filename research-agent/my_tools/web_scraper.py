"""
Example user tool: web page scraper.
Copy this directory to add custom tools.
Each .py file that exports a ToolSchema instance is auto-loaded.

Usage:
  from research_agent.tools import get_registry
  registry = get_registry()
  registry.load_from_dir("my_tools/")
"""
import httpx
from research_agent.tools.schema import ToolSchema, ToolResult


def _handle_scrape(params: dict, llm, state, emit) -> ToolResult:
    url = params.get("url", "")
    if not url.startswith("http"):
        return ToolResult.fail("Invalid URL")

    emit("tool", {"tool": "web_scrape", "status": "start", "url": url})
    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True)
        resp.raise_for_status()
        # Simple text extraction (in production, use a proper HTML parser)
        text = resp.text[:5000]
        emit("tool", {"tool": "web_scrape", "status": "done", "length": len(text)})
        return ToolResult.ok(content=text, length=len(text))
    except Exception as e:
        return ToolResult.fail(str(e))


web_scraper = ToolSchema(
    name="web_scrape",
    description="抓取指定 URL 的网页内容。用于获取最新博客、新闻或文档。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要抓取的网页完整 URL"}
        },
        "required": ["url"],
    },
    handler=_handle_scrape,
    category="user",
)