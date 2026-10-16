"""Benchmark bridge to the production RuntimeHost and injectable AgentKernel."""
import uuid
from research_agent.kernel import AgentKernel, KernelResult
from research_agent.task_evaluation import StrategySpec, TaskExecution, workspace_path


class DirectAnswerKernel(AgentKernel):
    """Explicit no-tools baseline; shares the same host and model as the candidate."""
    id = "direct_answer"

    def run(self, host, request):
        host.set_round(1)
        return KernelResult(host.answer(host.prepare()), 1)


def filesystem_registry():
    from research_agent.tools import ToolRegistry
    from research_agent.tools.builtin import _plugins
    registry = ToolRegistry()
    registry.install_plugin(next(plugin for plugin in _plugins() if plugin.id == "filesystem"), apply_enabled=False)
    return registry


def runtime_strategy(kernel, provider_factory, *, registry_factory=filesystem_registry,
                     adapter_factory=None, metadata=None):
    """Same host/fixture/policy across strategies; provider is created per trial."""
    def run(case, workspace):
        from research_agent.models import AgentState
        from research_agent.runtime import RuntimeContext, FunctionCallingRuntime
        from research_agent.model_adapter import ProviderModelAdapter
        from research_agent.sandbox import end_run
        state = AgentState(user_input=case.prompt, workspace_dir=str(workspace),
                           active_chat_id=uuid.uuid4().hex, constraints=list(case.constraints))
        state._execution_id = "eval-" + uuid.uuid4().hex
        registry = registry_factory()
        events, usages = [], []
        provider = provider_factory()
        delegate = (adapter_factory or ProviderModelAdapter)(provider)

        def usage_record(usage, model=None):
            usage = dict(usage)
            if "cost_usd" not in usage and all(isinstance(usage.get(key), int) and not isinstance(usage[key], bool)
                    for key in ("prompt_tokens", "completion_tokens")):
                from research_agent.telemetry import estimate_cost
                cached = usage.get("cached_tokens") or usage.get("prompt_cache_hit_tokens") or 0
                cost, known = estimate_cost(model or getattr(provider, "model", ""),
                    usage["prompt_tokens"], usage["completion_tokens"], cached)
                if known:
                    usage["cost_usd"] = cost
            usages.append(usage)

        def emit(kind, data):
            events.append({"type": kind, "data": data})
            if kind == "llm_usage":
                usage_record(data, data.get("model"))

        class MeteredAdapter:
            # Observe adapters directly; do not rely on the legacy callback wrapper.
            def select(self, messages, tools, tool_choice="auto"):
                try:
                    result = delegate.select(messages, tools, tool_choice)
                except BaseException:
                    usages.append({})
                    raise
                usage_record(result.get("usage") or {}, result.get("model"))
                return result

            def answer(self, messages, callback):
                before = len(usages)
                try:
                    return delegate.answer(messages, callback)
                finally:
                    if len(usages) == before:
                        usages.append({})

        def policy(state, name, params, emit):
            tool = registry.tools.get(name)
            if tool is None or tool.requires_approval:
                return "Benchmark has no interactive approval; tool denied"
            if tool.plugin_id == "filesystem":
                value = params.get("path") if name != "file_glob" else params.get("pattern")
                if value:
                    try:
                        workspace_path(workspace, value)
                    except ValueError as exc:
                        return str(exc)
            return None

        try:
            registry.bind_state(state)
            ctx = RuntimeContext(provider, case.prompt, state, registry=registry,
                workspace_dir=str(workspace), chat_id=state.active_chat_id, emit=emit,
                pre_tool_hook=policy, max_rounds=case.max_rounds, model_adapter=MeteredAdapter())
            FunctionCallingRuntime(kernel).run(ctx)
            # Totals are valid only if EVERY observed call reports the measurement.
            metrics = {}
            for key in ("prompt_tokens", "completion_tokens", "cost_usd"):
                values = [usage.get(key) for usage in usages]
                metrics[key] = sum(values) if values and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values) else None
            metrics["model_calls"] = len(usages)
            return TaskExecution(state.final_response, state.termination_reason, state.round_count,
                                 tuple(events), metrics)
        finally:
            end_run(state)
            registry.close_session(state.active_chat_id)
            for plugin_id in reversed(tuple(registry.plugins)):
                registry.disable_plugin(plugin_id, persist=False)

    from research_agent.telemetry import _price_hash
    from research_agent.model_adapter import ModelSettings
    from research_agent.config import get_context_config
    from dataclasses import asdict
    details = dict(metadata or {})
    details.update(cost_basis="reported_or_local_price_estimate", prices_version=_price_hash())
    details["model_settings"] = asdict(ModelSettings.from_config())
    context = get_context_config()
    details["context_settings"] = {key: context[key] for key in (
        "window_tokens", "output_tokens", "safety_tokens", "image_tokens", "tool_result_tokens") if key in context}
    return StrategySpec(kernel.id, run, metadata=details)
