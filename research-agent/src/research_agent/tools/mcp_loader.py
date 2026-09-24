"""MCP (Model Context Protocol) client — stdio JSON-RPC integration.
Connect external MCP servers, auto-register their tools."""
import json
import logging
import os
import subprocess
import threading
from queue import Queue, Empty

from research_agent.tools.schema import ToolSchema, ToolResult

logger = logging.getLogger(__name__)


def default_config_path() -> str:
    """Path to the MCP server config (env override: RESEARCH_AGENT_MCP_CONFIG)."""
    env = os.environ.get("RESEARCH_AGENT_MCP_CONFIG", "")
    if env:
        return env
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    return os.path.join(root, "skills", "mcp.yml")


def load_servers(config_path: str | None = None) -> list[dict]:
    path = config_path or default_config_path()
    if not os.path.isfile(path):
        return []
    try:
        import yaml
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:
        return []
    servers = data.get("servers") if isinstance(data, dict) else None
    return [s for s in (servers or []) if isinstance(s, dict)]


def save_servers(servers: list[dict], config_path: str | None = None) -> str:
    path = config_path or default_config_path()
    data = {}
    if os.path.isfile(path):
        try:
            import yaml
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except Exception:
            data = {}
    if not isinstance(data, dict):
        data = {}
    data["servers"] = servers
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    import yaml
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
    return path


def server_key(entry: dict) -> str:
    return entry.get("name") or " ".join(entry.get("command") or [])


def get_server(name: str, config_path: str | None = None) -> dict | None:
    for s in load_servers(config_path):
        if server_key(s) == name or s.get("name") == name:
            return s
    return None


def add_server(name: str, command: list[str], env: dict | None = None,
               config_path: str | None = None) -> dict:
    """Add/replace an MCP server entry (matched by name)."""
    servers = [s for s in load_servers(config_path)
               if s.get("name") != name and " ".join(s.get("command") or []) != name]
    entry: dict = {"name": name, "command": list(command)}
    if env:
        entry["env"] = {str(k): str(v) for k, v in env.items()}
    servers.append(entry)
    save_servers(servers, config_path)
    return entry


def remove_server(name: str, config_path: str | None = None) -> bool:
    servers = load_servers(config_path)
    kept = [s for s in servers if server_key(s) != name and s.get("name") != name]
    if len(kept) == len(servers):
        return False
    save_servers(kept, config_path)
    return True


class MCPClient:
    """Single MCP server connection: one process, stdin/stdout JSON-RPC."""

    def __init__(self, command: list[str], env: dict | None = None):
        self.command = command
        self.env = dict(env or {})
        self.process: subprocess.Popen | None = None
        self._rid = 0
        self._lock = threading.Lock()
        self._pending: dict[int, Queue] = {}
        self._reader: threading.Thread | None = None
        self.tool_names: list[str] = []
        self.healthy = False

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def connect(self) -> bool:
        try:
            child_env = {**os.environ, **{str(k): str(v) for k, v in self.env.items()}}
            self.process = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, errors="replace", bufsize=1,
                env=child_env,
            )
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
            resp = self._request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "PaperPilot", "version": "1.0"},
            })
            self.healthy = resp is not None
            return self.healthy
        except Exception:
            self.healthy = False
            return False

    def _read_loop(self):
        if not self.process or not self.process.stdout:
            return
        for line in self.process.stdout:
            try:
                msg = json.loads(line.strip())
                rid = msg.get("id")
                if rid is not None:
                    with self._lock:
                        q = self._pending.get(rid)
                    if q:
                        q.put(msg)
            except (json.JSONDecodeError, KeyError, BrokenPipeError):
                pass

    def _request(self, method: str, params: dict | None = None, timeout: float = 10.0) -> dict | None:
        if not self.process or not self.process.stdin:
            return None
        with self._lock:
            self._rid += 1
            rid = self._rid
        req = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        q: Queue = Queue()
        with self._lock:
            self._pending[rid] = q
        try:
            self.process.stdin.write(req + "\n")
            self.process.stdin.flush()
            result = q.get(timeout=timeout)
            return result.get("result")
        except (Empty, BrokenPipeError, OSError, ValueError) as e:
            self.healthy = False
            logger.warning(f"MCP request {method} failed: {e}")
            return None
        finally:
            with self._lock:
                self._pending.pop(rid, None)

    def list_tools(self) -> list[dict]:
        result = self._request("tools/list")
        if result and "tools" in result:
            return result["tools"]
        return []

    def call_tool(self, name: str, arguments: dict, timeout: float = 30.0) -> dict | None:
        return self._request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)

    def check_health(self) -> bool:
        if not self.alive:
            self.healthy = False
            return False
        try:
            result = self._request("ping", timeout=3.0)
            self.healthy = result is not None
            return self.healthy
        except Exception:
            self.healthy = False
            return False

    def close(self):
        if self.process:
            try:
                self.process.stdin.close()
            except Exception:
                pass
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
            self.healthy = False


class MCPManager:
    """Manages multiple MCP clients: parallel loading, health monitoring, graceful shutdown."""

    def __init__(self, config_path: str | None = None):
        self.clients: dict[str, MCPClient] = {}  # key: " ".join(command)
        self.config_path = config_path

    def load_config(self, config_path: str | None = None) -> list[dict]:
        path = config_path or self.config_path
        if not path:
            return []
        import os as _os
        if not _os.path.exists(path):
            return []
        import yaml
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
        return data.get("servers", [])

    def start_all(self, registry=None) -> dict[str, list[str]]:
        """Connect all configured MCP servers in parallel. Returns {command_key: [tool_names]}."""
        if registry is None:
            from research_agent.tools import get_registry
            registry = get_registry()

        servers = self.load_config()
        results: dict[str, list[str]] = {}
        threads: list[threading.Thread] = []

        def _load_one(entry: dict):
            cmd = entry.get("command", [])
            if not cmd:
                return
            name = entry.get("name", "")
            key = name or " ".join(cmd)
            try:
                registered = load_from_mcp(cmd, registry, manager=self,
                                           env=entry.get("env"), name=name)
                results[key] = registered
            except Exception as e:
                logger.warning(f"MCP server {key} failed to load: {e}")
                results[key] = []

        for entry in servers:
            t = threading.Thread(target=_load_one, args=(entry,), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=15)

        return results

    def health_check(self) -> dict[str, bool]:
        """Check health of all managed clients. Returns {key: healthy}."""
        return {key: client.check_health() for key, client in self.clients.items()}

    def reconnect_stale(self) -> dict[str, bool]:
        """Reconnect any unhealthy clients. Returns {key: reconnected}."""
        results = {}
        for key, client in list(self.clients.items()):
            if not client.healthy:
                logger.info(f"Reconnecting MCP server: {key}")
                try:
                    client.close()
                    ok = client.connect()
                    results[key] = ok
                except Exception:
                    results[key] = False
        return results

    def shutdown(self):
        """Close all managed MCP clients."""
        for key, client in list(self.clients.items()):
            try:
                client.close()
                logger.info(f"MCP server closed: {key}")
            except Exception:
                pass
        self.clients.clear()


_mcp_manager: MCPManager | None = None


def get_mcp_manager() -> MCPManager:
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPManager()
    return _mcp_manager


def load_from_mcp(command: list[str], registry=None, manager: MCPManager | None = None,
                  env: dict | None = None, name: str = "") -> list[str]:
    """Connect to MCP server and register its tools. Returns list of registered names."""
    if registry is None:
        from research_agent.tools import get_registry
        registry = get_registry()

    client = MCPClient(command, env=env)
    key = name or " ".join(command)

    if manager is None:
        manager = get_mcp_manager()
    manager.clients[key] = client

    if not client.connect():
        raise RuntimeError(f"Failed to connect to MCP server: {key}")

    tools = client.list_tools()
    registered = []

    for tool_info in tools:
        name = tool_info.get("name", "")
        if not name:
            continue

        mcp_name = f"mcp_{name}"
        client.tool_names.append(mcp_name)

        def _make_handler(tool_name: str, _client: MCPClient):
            def handler(params: dict, llm, state, emit) -> ToolResult:
                try:
                    result = _client.call_tool(tool_name, params)
                    if result is None:
                        return ToolResult.fail("MCP tool call returned None")
                    content = ""
                    for c in result.get("content", []):
                        if c.get("type") == "text":
                            content += c.get("text", "")
                    if "isError" in result and result["isError"]:
                        return ToolResult.fail(content or "MCP tool error")
                    return ToolResult.ok(content=content, raw=result)
                except Exception as e:
                    return ToolResult.fail(str(e))
            return handler

        schema = ToolSchema(
            name=mcp_name,
            description=tool_info.get("description", f"MCP tool: {name}"),
            parameters=tool_info.get("inputSchema", {"type": "object", "properties": {}}),
            handler=_make_handler(name, client),
            category="mcp",
            plugin_id="mcp",          # dynamic external-source plugin
            group=key,                # server key → group of tools from one server
            side_effect=True,         # unknown semantics → assume side effects possible
        )
        try:
            registry.register(schema)
            registered.append(mcp_name)
        except ValueError:
            pass

    return registered
