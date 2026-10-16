"""Built-in capability plugins.

Plugins are the unified capability unit: tool-bearing (filesystem/shell/
subagent/memory) and behavior-only (diagnostics, which hooks the event stream).
Each plugin declares its disk footprint so uninstall can remove files/data; each
is independently enable/disable-able at runtime via config `plugins.<id>`.

Core plugins (filesystem/shell/subagent) are installed by default and cannot be
uninstalled (is_core). register_builtins() reconciles every plugin's tools against
its current enabled state on each call.
"""
from dataclasses import replace

from research_agent.tools import get_registry
from research_agent.tools.schema import ToolPlugin, ToolSchema
from research_agent.tools.builtin.filesystem import (
    file_read_tool, file_write_tool, file_edit_tool, file_glob_tool, file_grep_tool,
    shell_exec_tool, check_tasks_tool, cancel_task_tool,
)
from research_agent.tools.builtin.context_results import context_result_tool
from research_agent.tools.builtin.git_inspect import git_inspect_tool
from research_agent.tools.builtin.network import web_fetch_tool, web_search_tool
from research_agent.tools.builtin.evolve import (
    record_experience_tool, classify_experience_tool, propose_skill_tool,
    list_experience_tool, list_skills_tool,
)
from research_agent.tools.builtin.telemetry import usage_report_tool, usage_query_tool
from research_agent.tools.builtin.plan import (
    plan_update_tool, plan_search_tool, plan_execute_tool,
)
from research_agent.tools.subagent import spawn_subagent_tool
from research_agent.plan_state import CURRENT_PLAN
from research_agent.file_views import FILE_VIEWS
from research_agent.sandbox import SESSION_FIELD, close_all_sessions
from research_agent.evaluation_tasks import filesystem_cases


def _stamp(tool: ToolSchema, plugin_id: str, group: str = "",
           requires_approval: bool = False, side_effect: bool = True) -> ToolSchema:
    return replace(tool, plugin_id=plugin_id, group=group,
                   requires_approval=requires_approval, side_effect=side_effect)


def _plugins() -> list[ToolPlugin]:
    return [
        # Pure workspace file operations — the "file processing" plugin.
        ToolPlugin(
            id="filesystem",
            state_components=[FILE_VIEWS],
            evaluation_cases=filesystem_cases(),
            label="工作区文件操作（读写/编辑/查找/搜索）",
            is_core=True,
            tools=[
                _stamp(git_inspect_tool, "filesystem", "git", side_effect=False),
                _stamp(context_result_tool, "filesystem", "context", side_effect=False),
                _stamp(file_read_tool, "filesystem", "file", side_effect=False),
                _stamp(file_write_tool, "filesystem", "file"),
                _stamp(file_edit_tool, "filesystem", "file"),
                _stamp(file_glob_tool, "filesystem", "file", side_effect=False),
                _stamp(file_grep_tool, "filesystem", "file", side_effect=False),
            ],
        ),
        # Command execution — core, dangerous by default.
        ToolPlugin(
            id="shell",
            state_components=[SESSION_FIELD],
            on_disable=close_all_sessions,
            label="Shell 命令执行与后台任务",
            is_core=True,
            tools=[
                _stamp(shell_exec_tool, "shell", "exec", requires_approval=True),
                _stamp(check_tasks_tool, "shell", "task", side_effect=False),
                _stamp(cancel_task_tool, "shell", "task"),
            ],
        ),
        # Parallel sub-agent orchestration.
        ToolPlugin(
            id="subagent",
            label="并行子代理编排（spawn_subagent）",
            is_core=True,
            tools=[
                _stamp(spawn_subagent_tool, "subagent", "orchestrate", side_effect=False),
            ],
        ),
        # Diagnostics — behavior-only capability (no tools): event stream, monitor,
        # scan/report hooks. owns diagnostics/ package + logs data.
        ToolPlugin(
            id="diagnostics",
            label="故障检测与开发者报告（事件流/监控/report）",
            enabled=True,
            owned_packages=["research_agent.diagnostics"],
            test_files=["tests/test_diagnostics.py", "tests/test_diagnostics_phase_e.py"],
            data_dirs=["logs", "diagnostics"],
        ),
        # Network access — opt-in (default disabled). Read-only web fetch/search
        # behind an SSRF-aware URL policy; see tools/builtin/network.py.
        ToolPlugin(
            id="web",
            label="联网检索与抓取（web_search / web_fetch）",
            enabled=False,
            tools=[
                _stamp(web_fetch_tool, "web", "fetch", side_effect=False),
                _stamp(web_search_tool, "web", "search", side_effect=False),
            ],
            owned_files=["research_agent/tools/builtin/network.py"],
            test_files=["tests/test_network_tools.py"],
        ),
        # Self-evolution — distill project experience into reusable user skills.
        # Writes (propose_skill) require human approval; no plugin authoring here.
        ToolPlugin(
            id="evolve",
            label="自进化（项目经验沉淀 → 用户技能）",
            enabled=True,
            tools=[
                _stamp(record_experience_tool, "evolve", "experience"),
                _stamp(classify_experience_tool, "evolve", "experience", side_effect=False),
                _stamp(propose_skill_tool, "evolve", "promote", requires_approval=True),
                _stamp(list_experience_tool, "evolve", "experience", side_effect=False),
                _stamp(list_skills_tool, "evolve", "experience", side_effect=False),
            ],
            owned_files=["research_agent/tools/builtin/evolve.py"],
            test_files=["tests/test_evolve.py"],
        ),
        # Runtime evaluation telemetry — host calls into research_agent.telemetry
        # when enabled; exposes read-only usage_report/usage_query tools.
        ToolPlugin(
            id="telemetry",
            label="运行时评测（token/费用/时延/工具路径）",
            enabled=True,
            tools=[
                _stamp(usage_report_tool, "telemetry", "usage", side_effect=False),
                _stamp(usage_query_tool, "telemetry", "usage", side_effect=False),
            ],
            owned_files=["research_agent/telemetry.py",
                         "research_agent/tools/builtin/telemetry.py"],
            test_files=["tests/test_telemetry.py"],
            data_dirs=["usage"],
        ),
        # Task planning (plan-as-memory): register a structured plan before
        # multi-step work; recall similar past plans. Plans persist under the
        # project data dir (see research_agent.plan), not the git workspace.
        ToolPlugin(
            id="plan",
            state_components=[CURRENT_PLAN],
            label="任务计划（plan-as-memory：登记 / 检索 / DAG 执行）",
            enabled=True,
            tools=[
                _stamp(plan_update_tool, "plan", "plan"),
                _stamp(plan_search_tool, "plan", "plan", side_effect=False),
                _stamp(plan_execute_tool, "plan", "plan"),
            ],
            owned_files=["research_agent/plan.py",
                         "research_agent/plan_executor.py",
                         "research_agent/tools/builtin/plan.py"],
            test_files=["tests/test_plan.py"],
        ),
        # MCP external tools — dynamic; the plugin is a placeholder whose tools are
        # added at runtime by mcp_loader (each server tools tagged plugin_id="mcp").
        ToolPlugin(
            id="mcp",            label="MCP 外部工具（动态加载）",
            enabled=True,
            owned_files=["research_agent/tools/mcp_loader.py"],
        ),
    ]


def register_builtins():
    """Install all capability plugins, reconciling tools with enabled state."""
    registry = get_registry()
    for plugin in _plugins():
        if plugin.id == "telemetry":
            from research_agent.evaluation import EvaluationSpec, telemetry_check
            plugin.evaluations.append(EvaluationSpec("usage", "runtime_cost", "harness", telemetry_check))
        registry.install_plugin(plugin, apply_enabled=True)
        if not plugin.enabled:
            # install_plugin registers tools only when enabled; ensure disabled
            # plugins have none exposed
            for t in plugin.tools:
                registry.unregister(t.name)
