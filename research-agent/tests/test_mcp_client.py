from unittest.mock import patch, MagicMock
from research_agent.mcp_client import load_mcp_config, MCPRegistry, connect_servers


def test_load_mcp_config_defaults(temp_data_dir):
    config = load_mcp_config()
    assert "servers" in config
    assert isinstance(config["servers"], list)


def test_load_mcp_config_with_file(temp_data_dir):
    import yaml
    config_path = temp_data_dir / "mcp_servers.yml"
    config_path.write_text("""
servers:
  - name: test-server
    command: python
    args: ["-c", "print('hello')"]
""")
    config = load_mcp_config()
    assert len(config["servers"]) >= 1


def test_mcp_registry_register_and_list():
    registry = MCPRegistry()
    registry.register("test-server", "search", {"description": "Search papers"})
    tools = registry.list_tools()
    assert len(tools) >= 1
    assert any(t["name"] == "search" for t in tools)


def test_mcp_registry_call_tool():
    registry = MCPRegistry()
    registry.register("test-server", "echo", {"description": "Echo"})
    with patch.object(registry, "_call_mcp_tool", return_value={"result": "hello"}):
        result = registry.call_tool("echo", {"message": "hello"})
        assert result["result"] == "hello"