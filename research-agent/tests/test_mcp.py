# tests/test_mcp.py — MCP config helpers, env passthrough, CLI add/list/remove.
import io

from research_agent.tools import mcp_loader as M


def test_config_add_list_remove(tmp_path):
    cfg = str(tmp_path / "mcp.yml")
    M.add_server("exa", ["npx", "-y", "mcporter", "run", "exa"],
                 env={"EXA_API_KEY": "x"}, config_path=cfg)
    servers = M.load_servers(cfg)
    assert len(servers) == 1
    assert M.server_key(servers[0]) == "exa"
    assert M.get_server("exa", cfg)["command"][0] == "npx"
    assert M.get_server("exa", cfg)["env"] == {"EXA_API_KEY": "x"}

    # add with same name replaces (no duplicate)
    M.add_server("exa", ["npx", "-y", "other"], config_path=cfg)
    assert len(M.load_servers(cfg)) == 1
    assert M.get_server("exa", cfg)["command"][-1] == "other"

    assert M.remove_server("exa", cfg) is True
    assert M.load_servers(cfg) == []
    assert M.remove_server("nope", cfg) is False


def test_server_key_prefers_name():
    assert M.server_key({"name": "exa", "command": ["npx", "x"]}) == "exa"
    assert M.server_key({"command": ["python", "s.py"]}) == "python s.py"


def test_client_merges_env_into_child(monkeypatch):
    captured = {}

    class _Proc:
        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = iter([])
            self.stderr = io.StringIO()

        def poll(self):
            return None

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        captured.update(kw)
        return _Proc()

    monkeypatch.setattr(M.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(M.MCPClient, "_request", lambda self, *a, **k: {})

    client = M.MCPClient(["echo", "hi"], env={"FOO": "bar"})
    assert client.connect() is True
    assert captured["env"].get("FOO") == "bar"
    assert "PATH" in captured["env"] or "Path" in captured["env"]  # inherited host env


def test_cli_add_list_remove(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from research_agent import cli as cli_mod

    cfg = str(tmp_path / "mcp.yml")
    monkeypatch.setenv("RESEARCH_AGENT_MCP_CONFIG", cfg)
    runner = CliRunner()

    res = runner.invoke(cli_mod.mcp, ["add", "exa", "--", "npx", "-y", "mcporter", "run", "exa"])
    assert res.exit_code == 0, res.output
    assert M.load_servers(cfg)[0]["name"] == "exa"

    res = runner.invoke(cli_mod.mcp, ["list"])
    assert res.exit_code == 0 and "exa" in res.output

    res = runner.invoke(cli_mod.mcp, ["remove", "exa"])
    assert res.exit_code == 0
    assert M.load_servers(cfg) == []

    res = runner.invoke(cli_mod.mcp, ["test", "nope"])
    assert res.exit_code == 1
