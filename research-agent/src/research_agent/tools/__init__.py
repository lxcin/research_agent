"""ToolRegistry - centralized tool & plugin management."""
from difflib import SequenceMatcher
from pathlib import Path
from contextlib import contextmanager, ExitStack
from functools import wraps

from research_agent.tools.schema import ToolSchema, ToolPlugin, ToolResult, EventCallback


def registry_locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._registry_lock:
            return method(self, *args, **kwargs)
    return call


def control_change(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        # Never hold the table lock while draining handlers: they may bind states.
        with self._control_lock:
            return method(self, *args, **kwargs)
    return call


class ToolRegistry:
    _instance: "ToolRegistry | None" = None

    def __init__(self):
        self._tools: dict[str, ToolSchema] = {}
        self._plugins: dict[str, ToolPlugin] = {}
        from research_agent.plugins import PluginManager
        self.lifecycle = PluginManager()
        from research_agent.evaluation import EvaluationRegistry, lifecycle_checks
        self.evaluations = EvaluationRegistry()
        for check in lifecycle_checks():
            self.evaluations.register("harness", check)
        from research_agent.evaluation import EvaluationSpec, quality_check
        self.evaluations.register("quality", EvaluationSpec("build_gate", "build", "harness", quality_check))
        self._tool_list_cache: list[dict] | None = None
        self._bound_states = []
        self._component_keys = {}
        from threading import RLock
        self._registry_lock = RLock()
        self._control_lock = RLock()
        self._session_lock = RLock()
        self._sessions = {}
        self._request_tokens = {}
        from research_agent.background_tasks import BackgroundTasks
        self.background_tasks = BackgroundTasks()

    def session_scope(self, session_id):
        from research_agent.plugins import ResourceScope, Lifetime
        with self._session_lock:
            if session_id not in self._sessions:
                scope = ResourceScope(session_id, Lifetime.SESSION)
                scope.on_drain(lambda: self._drain_session(session_id))
                self._sessions[session_id] = scope
            return self._sessions[session_id]

    def close_session(self, session_id, timeout=None):
        import time
        session_id = session_id or "default"
        budget = 10 if timeout is None else timeout
        deadline = time.monotonic() + budget
        self.session_scope(session_id).close(budget)
        self.background_tasks.wait_session_idle(session_id, max(0, deadline - time.monotonic()))

    def _drain_session(self, session_id):
        with self._session_lock:
            for token in self._request_tokens.get(session_id, ()):
                token.cancel()
        self.background_tasks.close_session(session_id)

    @contextmanager
    def request_scope(self, session_id, token):
        session_id = session_id or "default"
        token = self.background_tasks.session_token(session_id, token)
        with self._session_lock:
            old = self._sessions.get(session_id)
            if old is not None and old.closed:
                del self._sessions[session_id]  # a globally explicit reopen has occurred
            lease = self.session_scope(session_id).acquire()
            lease.__enter__()
            self._request_tokens.setdefault(session_id, set()).add(token)
        try:
            yield token
        finally:
            with self._session_lock:
                self._request_tokens[session_id].discard(token)
                if not self._request_tokens[session_id]:
                    del self._request_tokens[session_id]
            lease.__exit__(None, None, None)

    def reopen_session(self, session_id):
        from research_agent.plugins import ResourceScope, Lifetime
        with self._session_lock:
            old = self._sessions.get(session_id)
            if old is not None and not old.closed:
                raise RuntimeError("Session must finish draining before reopening")
            self.background_tasks.reopen_session(session_id)
            self._sessions[session_id] = ResourceScope(session_id, Lifetime.SESSION)
            self._sessions[session_id].on_drain(lambda: self._drain_session(session_id))

    @registry_locked
    def bind_state(self, state):
        """Inject active plugin components and reconcile previously bound states."""
        import weakref
        self._bound_states = [ref for ref in self._bound_states if ref() is not None]
        live_ids = {id(ref()) for ref in self._bound_states}
        self._component_keys = {key: value for key, value in self._component_keys.items()
                                if key in live_ids}
        if not any(ref() is state for ref in self._bound_states):
            self._bound_states.append(weakref.ref(state))
        self._sync_state_components()

    @registry_locked
    def _sync_state_components(self):
        active = {}
        for plugin in self._plugins.values():
            if plugin.enabled:
                for spec in plugin.state_components:
                    if not spec.key.startswith(plugin.id + "."):
                        raise ValueError("Plugin component keys must start with plugin_id + '.'")
                    if spec.key in active:
                        raise ValueError(f"Duplicate component: {spec.key}")
                    from dataclasses import replace
                    if spec.owner and spec.owner != plugin.id:
                        raise ValueError("Plugin cannot declare another owner state")
                    active[spec.key] = replace(spec, owner=plugin.id)
        for ref in self._bound_states:
            state = ref()
            if state is None:
                continue
            previous = self._component_keys.get(id(state), set())
            for key in previous - active.keys():
                state.components.remove(key)
            for spec in active.values():
                state.components.inject(spec)
            self._component_keys[id(state)] = set(active)

    @registry_locked
    def __contains__(self, name: str) -> bool:
        return name in self._tools

    # ── Tool registration (single tool, agent granularity) ──

    @registry_locked
    def register(self, tool: ToolSchema):
        from research_agent.tools.validate_params import check_schema
        check_schema(tool.parameters)
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
        ctx = self.lifecycle.contexts.get(tool.plugin_id)
        if ctx is not None:
            from dataclasses import replace
            handler = tool.handler
            def guarded(*args, **kwargs):
                with ctx.scope.acquire():
                    return handler(*args, **kwargs)
            tool = replace(tool, handler=guarded)
            ctx.scope.own(lambda: self.unregister(tool.name))
        self._tools[tool.name] = tool
        self._tool_list_cache = None

    @registry_locked
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
        self._sync_state_components()
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

    @control_change
    def install_plugin(self, plugin: ToolPlugin, apply_enabled: bool = True):
        from research_agent.plugins import PluginDeclaration
        keys = [spec.key for spec in plugin.state_components]
        if any(spec.owner and spec.owner != plugin.id for spec in plugin.state_components):
            raise ValueError("Plugin cannot declare another owner state")
        if len(keys) != len(set(keys)) or any(not key.startswith(plugin.id + ".") for key in keys):
            raise ValueError("Invalid plugin state fields")
        if any(t.plugin_id and t.plugin_id != plugin.id for t in plugin.tools):
            raise ValueError("Tool plugin ownership mismatch")
        missing = [key for key in plugin.depends if key not in self._plugins]
        if missing:
            raise ValueError(f"Missing dependency plugins: {missing}")
        enabled = self.plugin_enabled(plugin.id, plugin.enabled) if apply_enabled else plugin.enabled
        if plugin.id in self._plugins:
            if enabled:
                self.enable_plugin(plugin.id, persist=False)
            else:
                self.disable_plugin(plugin.id, persist=False)
            return
        plugin.enabled = enabled
        with self._registry_lock:
            self._plugins[plugin.id] = plugin
        def apply(ctx):
            if plugin.id == "shell":
                self.background_tasks.resume()
                ctx.scope.on_drain(self.background_tasks.stop)
                ctx.scope.own(self.background_tasks.drain)
            if plugin.id == "plan":
                from research_agent import plan_pipeline
                plan_pipeline.resume()
                ctx.scope.on_drain(plan_pipeline.pause)
            for spec in plugin.evaluations:
                ctx.scope.own(self.evaluations.register(plugin.id, spec, ctx.scope.acquire))
            for case in plugin.evaluation_cases:
                ctx.scope.own(self.evaluations.register_case(plugin.id, case, ctx.scope.acquire))
            for tool in plugin.tools:
                for spec in tool.evaluations:
                    ctx.scope.own(self.evaluations.register(plugin.id + "/" + tool.name, spec, ctx.scope.acquire))
                for case in tool.evaluation_cases:
                    ctx.scope.own(self.evaluations.register_case(plugin.id + "/" + tool.name, case, ctx.scope.acquire))
            if plugin.on_enable:
                plugin.on_enable()
            if plugin.on_disable:
                ctx.scope.own(plugin.on_disable)
            for tool in plugin.tools:
                ctx.register_tool(self, tool)
            ctx.provide("plugin." + plugin.id, plugin)
        declaration = PluginDeclaration(plugin.id, apply,
            requires=tuple("plugin." + key for key in plugin.depends),
            provides=("plugin." + plugin.id,))
        if plugin.on_install:
            plugin.on_install()
        # Declare disabled plugins without briefly activating their resources.
        self.lifecycle.declarations[plugin.id] = declaration
        self.lifecycle.phases[plugin.id] = "pending" if enabled else "disposed"
        if enabled:
            self.lifecycle.enabled.add(plugin.id)
        self.lifecycle.start()
        self._reconcile_lifecycle()

    @registry_locked
    def _reconcile_lifecycle(self):
        for key, plugin in self._plugins.items():
            plugin.enabled = self.lifecycle.phases.get(key) == "active"
        self._sync_state_components()

    def evaluate(self, events=(), artifacts=None):
        from research_agent.evaluation import EvaluationInput
        data = dict(artifacts or {})
        from research_agent.config import get_data_dir
        from research_agent.evidence import read
        for key, value in read(get_data_dir() / "evidence" / "lifecycle.db").items():
            data.setdefault(key, value)
        return self.evaluations.run(EvaluationInput(tuple(events), data))

    @control_change
    def enable_plugin(self, plugin_id: str, persist: bool = True) -> bool:
        if plugin_id not in self._plugins:
            return False
        self.lifecycle.enable(plugin_id)
        self._reconcile_lifecycle()
        if persist:
            self._set_plugin_config(plugin_id, True)
        return True

    @control_change
    def disable_plugin(self, plugin_id: str, persist: bool = True) -> bool:
        if plugin_id not in self._plugins:
            return False
        self.lifecycle.disable(plugin_id)
        self._reconcile_lifecycle()
        if persist:
            self._set_plugin_config(plugin_id, False)
        return True

    @control_change
    def uninstall_plugin(self, plugin_id: str, force: bool = False) -> bool:
        if plugin_id not in self._plugins:
            return False
        plugin = self._plugins[plugin_id]
        if plugin.is_core and not force:
            raise ValueError(f"Cannot uninstall core plugin '{plugin_id}'")
        dependents = [key for key, p in self._plugins.items() if plugin_id in p.depends]
        if dependents and not force:
            raise ValueError(f"Plugin still depended on by {dependents}")
        self.lifecycle.uninstall(plugin_id)
        with self._registry_lock:
            self._plugins.pop(plugin_id)
        self._reconcile_lifecycle()
        if plugin.on_uninstall:
            plugin.on_uninstall()
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
    @registry_locked
    def plugins(self) -> dict[str, ToolPlugin]:
        return dict(self._plugins)

    @registry_locked
    def get_plugin(self, plugin_id: str) -> ToolPlugin | None:
        return self._plugins.get(plugin_id)

    @registry_locked
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

    @registry_locked
    def list_for_llm(self) -> list[dict]:
        """Generate OpenAI-compatible tool list for function calling."""
        if self._tool_list_cache is None:
            self._tool_list_cache = [t.to_openai_schema() for t in self._tools.values()]
        return self._tool_list_cache

    @registry_locked
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
            if tool.usage:
                lines.append("  使用约定：" + tool.usage)
        return "\n".join(lines)

    # ── Dispatch ──

    @contextmanager
    def _admit_tool(self, name):
        with ExitStack() as leases:
            with self._registry_lock:
                tool = self._tools.get(name)
                if tool is not None and tool.plugin_id in self._plugins:
                    ctx = self.lifecycle.contexts.get(tool.plugin_id)
                    if ctx is None:
                        raise RuntimeError("Tool plugin is not active")
                    leases.enter_context(ctx.scope.acquire())
            # Lease covers validation, handler AND durable-state replay/recording.
            yield tool

    def dispatch(self, name: str, params: dict, llm, state, emit: EventCallback) -> ToolResult:
        """Execute a tool by name with given parameters."""
        try:
            with self._admit_tool(name) as tool:
                if tool is None:
                    return ToolResult.fail(f"Unknown tool: {name}")
                return self._dispatch(tool, params, llm, state, emit)
        except Exception as e:
            return ToolResult.fail(f"Tool '{name}' failed: {e}")

    def _dispatch(self, tool, params, llm, state, emit):
        name = tool.name
        from research_agent.tools.validate_params import validate_schema_params
        error = validate_schema_params(tool.parameters, params)
        if error:
            return ToolResult.fail(error)
        def invoke():
            from research_agent.model_retry import ModelUnavailable, PartialResponseError, retryable
            from research_agent.tools.schema import ToolNetworkError
            try:
                result = tool.handler(params, llm, state, emit)
            except (Exception, ModelUnavailable, PartialResponseError) as exc:
                cause = exc.cause if isinstance(exc, PartialResponseError) else exc
                if not isinstance(exc, (ToolNetworkError, ModelUnavailable)) and not retryable(cause):
                    raise
                if tool.side_effect and not (isinstance(exc, ToolNetworkError) and exc.confirmed_failed):
                    from research_agent.execution import RecoveryRequired
                    raise RecoveryRequired(f"Tool '{name}' network interrupted; side-effect outcome unknown, reconcile before retrying") from exc
                # This failed observation is committed by execute_once, so a
                # crash cannot turn a known failure into an uncertain attempt.
                return ToolResult.network_error(str(exc))
            if not isinstance(result, ToolResult):
                raise TypeError("Tool handler must return ToolResult")
            return result
        try:
            with self.session_scope(state.active_chat_id or "default").acquire():
                token = getattr(state, "_cancellation", None)
                if token is not None:
                    token.check()
                run_id = getattr(state, "_execution_id", None)
                call_id = getattr(state, "_current_tool_id", None)
                if run_id and call_id:
                    from research_agent.execution import execute_once
                    import hashlib, json
                    contract = hashlib.sha256(json.dumps([tool.version, tool.parameters], sort_keys=True).encode()).hexdigest()
                    return execute_once(run_id, call_id, name, params,
                                        invoke, state=state, contract_version=contract)
                return invoke()
        except Exception as e:
            return ToolResult.fail(f"Tool '{name}' failed: {str(e)}")

    # ── Skills ──

    @registry_locked
    def find_skill(self, user_input: str) -> ToolSchema | None:
        """Find a skill-type tool that matches the user input."""
        for tool in self._tools.values():
            if tool.category == "skill" and tool.triggers:
                for trigger in tool.triggers:
                    if trigger.lower() in user_input.lower():
                        return tool
        return None

    @property
    @registry_locked
    def tools(self) -> dict[str, ToolSchema]:
        return dict(self._tools)

    @property
    @registry_locked
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
