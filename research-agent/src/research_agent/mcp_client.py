"""MCP protocol client: connect to MCP servers, register tools, call tools."""
import json
import subprocess
import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from research_agent.config import get_data_dir


def load_mcp_config() -> dict:
    config_path = get_data_dir() / "mcp_servers.yml"
    if not config_path.exists():
        config_path.write_text("servers: []\n")
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {"servers": []}


@dataclass
class MCPTool:
    server_name: str = ""
    name: str = ""
    description: str = ""
    parameters: dict = field(default_factory=dict)
    require_confirm: bool = False


class MCPRegistry:
    def __init__(self):
        self._tools: dict[str, MCPTool] = {}
        self._server_processes: dict[str, subprocess.Popen] = {}

    def register(self, server_name: str, tool_name: str, metadata: dict):
        self._tools[tool_name] = MCPTool(
            server_name=server_name,
            name=tool_name,
            description=metadata.get("description", ""),
            parameters=metadata.get("parameters", {}),
            require_confirm=metadata.get("require_confirm", False),
        )

    def list_tools(self) -> list[dict]:
        return [{
            "name": t.name,
            "server": t.server_name,
            "description": t.description,
            "require_confirm": t.require_confirm,
        } for t in self._tools.values()]

    def get_tool(self, name: str) -> MCPTool | None:
        return self._tools.get(name)

    def call_tool(self, tool_name: str, args: dict) -> dict:
        tool = self._tools.get(tool_name)
        if not tool:
            return {"error": f"Tool '{tool_name}' not found in registry"}
        return self._call_mcp_tool(tool.server_name, tool_name, args)

    def _call_mcp_tool(self, server_name: str, tool_name: str, args: dict) -> dict:
        request = json.dumps({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": args},
            "id": 1,
        })
        proc = self._server_processes.get(server_name)
        if not proc:
            return {"error": f"Server '{server_name}' not connected"}
        try:
            proc.stdin.write(request + "\n")
            proc.stdin.flush()
            response_line = proc.stdout.readline()
            return json.loads(response_line)
        except Exception as e:
            return {"error": str(e)}

    def stop_all(self):
        for name, proc in self._server_processes.items():
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    def start_server(self, name: str, command: str, args: list[str]) -> bool:
        try:
            proc = subprocess.Popen(
                [command] + args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._server_processes[name] = proc
            init_req = json.dumps({
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {}},
                "id": 0,
            })
            proc.stdin.write(init_req + "\n")
            proc.stdin.flush()
            response = proc.stdout.readline()
            resp_data = json.loads(response)
            if "error" in resp_data:
                return False
            tools_req = json.dumps({
                "jsonrpc": "2.0",
                "method": "tools/list",
                "params": {},
                "id": 2,
            })
            proc.stdin.write(tools_req + "\n")
            proc.stdin.flush()
            tools_resp = proc.stdout.readline()
            tools_data = json.loads(tools_resp)
            for tool in tools_data.get("result", {}).get("tools", []):
                self.register(name, tool["name"], {
                    "description": tool.get("description", ""),
                    "parameters": tool.get("inputSchema", {}),
                })
            return True
        except Exception as e:
            return False


def connect_servers(config: dict) -> MCPRegistry:
    registry = MCPRegistry()
    for server_cfg in config.get("servers", []):
        name = server_cfg.get("name", "unknown")
        command = server_cfg.get("command", "")
        args = server_cfg.get("args", [])
        if command:
            registry.start_server(name, command, args)
    return registry