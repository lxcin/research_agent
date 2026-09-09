"""ToolRegistry - centralized tool & plugin management."""
from difflib import SequenceMatcher
from pathlib import Path

from research_agent.tools.schema import ToolSchema, ToolPlugin, ToolResult, EventCallback


class ToolRegistry:
    _instance: "ToolRegistry | None" = None

    def __init__(self):
        self._tools: dict[str, ToolSchema] = {}
        self._plugins: dict[str, ToolPlugin] = {}
        self._tool_list_cache: list[dict] | None = None

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    # ── Tool registration (single tool, agent granularity) ──

    def register(self, tool: ToolSchema):
        if tool.name in self._tools:
            existing = self._tools[tool.name]
            if existing.category != tool.category:
                raise ValueError(f"Tool '{tool.name}' already registered as '{existing.category}', cannot re-register as '{existing.category}'")
            return
        # Functional dedup check
        for name, existing in self._tools.items():
            if _descriptions_similar(tool.description, existing.description, threshold=0.80):
                raise ValueError(
                    f"Tool '{tool.name}' description is too similar to '{name}'. "
                    f"Please refine the description or remove the duplicate."
                )
        self._tools[tool.name] = tool
        self._tool_list_cache = None

    def unregister(self, name: str):
        if name in self._tools:
            del self._tools[name]
            self._tool_list_cache = None

    # ── Plugin registration (engineering granularity) ──

    def _config(self) -> dict:
        from research_agent.config import load_config
        cfg = load_config()
        plugins = cfg.get("plugins", {})
        return plugins if isinstance(plugins, dict) else {}

    def plugin_enabled(self, plugin_id: str, default: bool = True) -> bool:
        """Resolve enabled state: config `plugins.<id>.enabled` overrides default."""
        entry = self._config().get(plugin_id)
        if isinstance(entry, dict) and "enabled" in entry:
            return bool(entry["enabled"])
        return default

    def _set_plugin_config(self, plugin_id: str, enabled: bool):
        from research_agent.config import get_config_path, load_config
        cfg = load_config()
        plugins = cfg.setdefault("plugins", {})
        if not isinstance(plugins, dict):
            plugins = {}
            cfg["plugins"] = plugins
        entry = plugins.get(plugin_id, {})
        if not isinstance(entry, dict):
            entry = {}
        entry["enabled"] = enabled
        plugins[plugin_id] = entry
        with open(get_config_path(), "w", encoding="utf-8") as fh:
            import yaml
            yaml.safe_dump(cfg, fh, allow_unicode=True, sort_keys=False)

    def install_plugin(self, plugin: ToolPlugin, apply_enabled: bool = True):
        """Install a plugin: dependency check, register tools (if enabled), record disk footprint."""
        if plugin.id in self._plugins:
            # idempotent reinstall
            self._plugins[plugin.id] = plugin
            if plugin.enabled:
                for t in plugin.tools:
                    self.register(t)
            if plugin.on_install:
                try:
                    plugin.on_install()
                except Exception:
                    pass
            return
        missing = [d for d in plugin.depends if d not in self._plugins]
        if missing:
            raise ValueError(
                f"Cannot install plugin '{plugin.id}': missing dependency plugins {missing}"
            )
        for t in plugin.tools:
            if t.plugin_id and t.plugin_id != plugin.id:
                raise ValueError(
                    f"Tool '{t.name}' declares plugin_id='{t.plugin_id}' but is in plugin '{plugin.id}'"
                )
        if apply_enabled:
            plugin.enabled = self.plugin_enabled(plugin.id, plugin.enabled)
        self._plugins[plugin.id] = plugin
        if plugin.on_install:
            try:
                plugin.on_install()
            except Exception:
                pass
        if plugin.enabled:
            for t in plugin.tools:
                self.register(t)

    def enable_plugin(self, plugin_id: str) -> bool:
        """Runtime-enable: register tools (if installed), fire on_enable, persist config."""
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            return False
        if not plugin.enabled:
            for t in plugin.tools:
                self.register(t)
            plugin.enabled = True
            if plugin.on_enable:
                try:
                    plugin.on_enable()
                except Exception:
                    pass
        self._set_plugin_config(plugin_id, True)
        return True

    def disable_plugin(self, plugin_id: str) -> bool:
        """Runtime-disable: unregister tools immediately, fire on_disable, persist config."""
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            return False
        if plugin.enabled:
            for t in plugin.tools:
                self.unregister(t.name)
            plugin.enabled = False
            if plugin.on_disable:
                try:
                    plugin.on_disable()
                except Exception:
                    pass
        self._set_plugin_config(plugin_id, False)
        return True

    def uninstall_plugin(self, plugin_id: str, force: bool = False) -> bool:
        """Uninstall a plugin: disable tools, persist config, remove disk footprint,
        fire on_uninstall. Refuses if other enabled plugins depend on it."""
        if plugin_id not in self._plugins:
            return False
        plugin = self._plugins[plugin_id]
        if plugin.is_core and not force:
            raise ValueError(f"Cannot uninstall core plugin '{plugin_id}'")
        dependents = [pid for pid, p in self._plugins.items()
                      if plugin_id in p.depends and pid != plugin_id]
        if dependents and not force:
            raise ValueError(
                f"Cannot uninstall plugin '{plugin_id}': still depended on by {dependents}"
            )
        # unregister tools
        for t in plugin.tools:
            self.unregister(t.name)
        self._plugins.pop(plugin_id)
        if plugin.on_uninstall:
            try:
                plugin.on_uninstall()
            except Exception:
                pass
        self._set_plugin_config(plugin_id, False)
        return True

    def remove_plugin_disk(self, plugin: ToolPlugin, src_root: str, data_root: str | None = None) -> list[str]:
        """Delete a plugin's owned files/packages/tests/data dirs. Returns removed paths."""
        import os
        import shutil
        removed: list[str] = []
        for rel in plugin.owned_files:
            p = os.path.join(src_root, rel)
            if os.path.isfile(p):
                os.remove(p)
                removed.append(rel)
        for pkg in plugin.owned_packages:
            pkg_dir = os.path.join(src_root, *pkg.split(".")[1:])
            if os.path.isdir(pkg_dir):
                shutil.rmtree(pkg_dir, ignore_errors=True)
                removed.append(pkg)
            elif os.path.isfile(pkg_dir + ".py"):
                os.remove(pkg_dir + ".py")
                removed.append(pkg)
        for rel in plugin.test_files:
            p = os.path.join(src_root, rel)
            if os.path.isfile(p):
                os.remove(p)
                removed.append(rel)
        if data_root:
            for d in plugin.data_dirs:
                dp = os.path.join(data_root, d)
                if os.path.isdir(dp):
                    shutil.rmtree(dp, ignore_errors=True)
                    removed.append(f"data_dir/{d}")
        return removed

    @property
    def plugins(self) -> dict[str, ToolPlugin]:
        return dict(self._plugins)

    def get_plugin(self, plugin_id: str) -> ToolPlugin | None:
        return self._plugins.get(plugin_id)

    def tools_of(self, plugin_id: str) -> list[ToolSchema]:
        plugin = self._plugins.get(plugin_id)
        return list(plugin.tools) if plugin else []

    # ── User tool loading ──

    def load_from_dir(self, dir_path: str):
        """Scan a directory for .py files and register any ToolSchema instances."""
        import importlib.util
        p = Path(dir_path)
        if not p.is_dir():
            raise FileNotFoundError(f"Directory not found: {dir_path}")
        for f in sorted(p.glob("*.py")):
            if f.name.startswith("_"):
                continue
            spec = importlib.util.spec_from_file_location(f"user_tool_{f.stem}", str(f))
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for attr_name in dir(mod):
                obj = getattr(mod, attr_name)
                if isinstance(obj, ToolSchema):
                    self.register(obj)

    # ── LLM integration ──

    def list_for_llm(self) -> list[dict]:
        """Generate OpenAI-compatible tool list for function calling."""
        if self._tool_list_cache is None:
            self._tool_list_cache = [t.to_openai_schema() for t in self._tools.values()]
        return self._tool_list_cache

    def generate_capabilities(self, tool_names: list[str] | None = None) -> str:
        """Generate a human-readable capability description.
        If tool_names is provided, only describe those tools.
        Injected into the system prompt so the LLM knows what it can do."""
        tools = {k: v for k, v in self._tools.items() if tool_names is None or k in tool_names} if tool_names else self._tools
        if not tools:
            return "你目前没有任何可用工具。"
        lines = ["可用工具："]
        for tool in tools.values():
            lines.append(f"- {tool.name}: {tool.description}")
        return "\n".join(lines)

    # ── Dispatch ──

    def dispatch(self, name: str, params: dict, llm, state, emit: EventCallback) -> ToolResult:
        """Execute a tool by name with given parameters."""
        if name not in self._tools:
            return ToolResult.fail(f"Unknown tool: {name}")
        tool = self._tools[name]
        try:
            return tool.handler(params, llm, state, emit)
        except Exception as e:
            return ToolResult.fail(f"Tool '{name}' failed: {str(e)}")

    # ── Skills ──

    def find_skill(self, user_input: str) -> ToolSchema | None:
        """Find a skill-type tool that matches the user input."""
        for tool in self._tools.values():
            if tool.category == "skill" and tool.triggers:
                for trigger in tool.triggers:
                    if trigger.lower() in user_input.lower():
                        return tool
        return None

    @property
    def tools(self) -> dict[str, ToolSchema]:
        return dict(self._tools)

    @property
    def count(self) -> int:
        return len(self._tools)


def _descriptions_similar(a: str, b: str, threshold: float = 0.80) -> bool:
    """Check if two tool descriptions are too similar."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() > threshold


# Global singleton
def get_registry() -> ToolRegistry:
    if ToolRegistry._instance is None:
        ToolRegistry._instance = ToolRegistry()
    return ToolRegistry._instance


def is_plugin_enabled(plugin_id: str, default: bool = True) -> bool:
    """Config-backed gate for a plugin (works even before install)."""
    reg = get_registry()
    # if installed, trust its live state first
    if plugin_id in reg._plugins:
        return reg._plugins[plugin_id].enabled
    return reg.plugin_enabled(plugin_id, default=default)