"""
Example user tool: file I/O for reading/writing code, CSV, JSON, etc.
Adding this file to my_tools/ gives the agent ability to write and run code.
"""
import os
from research_agent.tools.schema import ToolSchema, ToolResult


def _handle_file_read(params: dict, llm, state, emit) -> ToolResult:
    path = params.get("path", "")
    if not os.path.exists(path):
        return ToolResult.fail(f"File not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()[:8000]
        return ToolResult.ok(content=content, path=path, size=len(content))
    except Exception as e:
        return ToolResult.fail(str(e))


def _handle_file_write(params: dict, llm, state, emit) -> ToolResult:
    path = params.get("path", "")
    content = params.get("content", "")
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return ToolResult.ok(path=path, written=len(content))
    except Exception as e:
        return ToolResult.fail(str(e))


file_read = ToolSchema(
    name="file_read",
    description="读取本地文件内容（Python代码、CSV、JSON、TXT等）。用于查看代码或数据。",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "文件路径"}},
        "required": ["path"],
    },
    handler=_handle_file_read,
    category="user",
)

file_write = ToolSchema(
    name="file_write",
    description="写入内容到本地文件。可用于保存代码、CSV数据、JSON结果等。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目标文件路径"},
            "content": {"type": "string", "description": "要写入的内容"},
        },
        "required": ["path", "content"],
    },
    handler=_handle_file_write,
    category="user",
)