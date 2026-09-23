"""CLI entry point for PaperPilot research agent."""
import os
import sys

import click

from research_agent.agent import AgentState
from research_agent.llm import LiteLLMProvider
from research_agent.config import get_api_key, get_model_config


@click.group(context_settings={"help_option_names": ["-h", "--help"]},
             invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context):
    """PaperPilot - Research Agent CLI.

    Run bare (default) or `research-agent chat` for interactive chat;
    `research-agent diagnose` for developer diagnostics.
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(chat_cmd)


@main.command()
@click.option("--model", default=None, help="LLM model name (e.g. deepseek/deepseek-chat)")
@click.option("--api-key", default=None, help="API key for the LLM provider")
@click.option("--api-base", default=None, help="Custom API base URL")
def chat_cmd(model: str | None, api_key: str | None, api_base: str | None):
    """Start interactive chat session."""
    key = api_key or get_api_key()
    if not key:
        click.echo("Error: No API key configured. Use --api-key or set ANTHROPIC_API_KEY.", err=True)
        sys.exit(1)

    model_name = model or get_model_config().get("name", "deepseek/deepseek-chat")
    click.echo(f"PaperPilot CLI (model: {model_name})")
    click.echo("Type /exit to quit, /new to start a new conversation.\n")

    state = AgentState()
    while True:
        try:
            user_input = click.prompt("You", prompt_suffix="> ").strip()
        except (EOFError, KeyboardInterrupt):
            click.echo("\nGoodbye!")
            break

        if user_input.lower() in ("/exit", "/quit", ":q"):
            click.echo("Goodbye!")
            break
        if user_input.lower() in ("/new", "/reset"):
            state = AgentState()
            click.echo("Starting a new conversation.\n")
            continue
        if not user_input:
            continue

        click.echo()
        llm = LiteLLMProvider(model=model_name, api_key=key, api_base=api_base)
        from research_agent.agent import run_agent
        result = run_agent(user_input, llm, state)
        click.echo(result.final_response or "(no response)")
        _handle_proposals(result)
        click.echo()


def _render_proposals(changes) -> None:
    for i, c in enumerate(changes, 1):
        sign = {"added": "+", "deleted": "-", "modified": "~"}.get(c.status, "?")
        click.echo(f"  [{i}] {sign} {c.path}  (+{c.additions} -{c.deletions})")


def _handle_proposals(state) -> None:
    """Render pending file changes and let the user keep/undo per file or all.

    Commands: keep all | undo all | keep 1,3 | undo 2 | diff 2 | done
    """
    changes = list(getattr(state, "pending_proposals", []) or [])
    if not changes:
        return
    workspace = getattr(state, "workspace_dir", "") or ""
    from research_agent.proposal import ProposalManager
    pmgr = ProposalManager(workspace)

    click.echo()
    click.echo(f"检测到 {len(changes)} 个文件改动（未提交）。keep=保留并提交，undo=撤销：")
    _render_proposals(changes)
    click.echo("  命令: keep all | undo all | keep 1,3 | undo 2 | diff 2 | done")

    def _reset(state, remaining_paths):
        props = pmgr.collect(remaining_paths) if remaining_paths else []
        state.pending_proposals = props
        return props

    while True:
        try:
            cmd = click.prompt("proposal", prompt_suffix="> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            click.echo()
            break
        if cmd in ("done", "d", ""):
            click.echo("已保留为未提交状态（可随时用 git 自行处理）。")
            break

        parts = cmd.replace(",", " ").split()
        action = parts[0] if parts else ""
        targets = parts[1:]

        if action in ("keep", "k"):
            if targets and targets[0] == "all":
                pmgr.keep_all()
                state.pending_proposals = []
                click.echo("已保留全部改动。")
                break
            indexes = [int(x) for x in targets if x.isdigit()]
            paths = [changes[i - 1].path for i in indexes if 1 <= i <= len(changes)]
            if paths:
                pmgr.keep(paths)
                remaining = [c.path for c in changes if c.path not in paths]
                changes = _reset(state, remaining)
                click.echo(f"已保留 {len(paths)} 个文件。剩余 {len(changes)} 个。")
                if not changes:
                    break
                _render_proposals(changes)
            else:
                click.echo("用法: keep 1,3 或 keep all")
        elif action in ("undo", "u"):
            if targets and targets[0] == "all":
                pmgr.undo_all()
                state.pending_proposals = []
                click.echo("已撤销全部改动。")
                break
            indexes = [int(x) for x in targets if x.isdigit()]
            paths = [changes[i - 1].path for i in indexes if 1 <= i <= len(changes)]
            if paths:
                pmgr.undo(paths)
                remaining = [c.path for c in changes if c.path not in paths]
                changes = _reset(state, remaining)
                click.echo(f"已撤销 {len(paths)} 个文件。剩余 {len(changes)} 个。")
                if not changes:
                    break
                _render_proposals(changes)
            else:
                click.echo("用法: undo 2 或 undo all")
        elif action in ("diff",):
            indexes = [int(x) for x in targets if x.isdigit()]
            if indexes and 1 <= indexes[0] <= len(changes):
                click.echo(changes[indexes[0] - 1].diff or "(no diff)")
            else:
                click.echo("用法: diff 1")
        else:
            click.echo("命令: keep all | undo all | keep 1,3 | undo 2 | diff 2 | done")


@main.command()
@click.option("--limit", default=20, help="最近扫描的会话数 (default 20)")
@click.option("--report", "write_report", is_flag=True, default=False,
              help="生成 report-{ts}.md/.json 到 data_dir/diagnostics/")
@click.option("--llm", "use_llm", is_flag=True, default=False,
              help="用小模型对低质量会话做语义标注（需 API key）")
def diagnose(limit: int, write_report: bool, use_llm: bool):
    """开发者诊断：扫描最近会话，汇总故障与质量指标."""
    from research_agent.diagnostics import scan as diag_scan
    from research_agent.diagnostics import report as diag_report

    llm = None
    if use_llm:
        key = get_api_key()
        if key:
            model = get_model_config().get("name", "deepseek/deepseek-chat")
            llm = LiteLLMProvider(model=model, api_key=key)
        else:
            click.echo("警告: --llm 需要 API key，已跳过语义标注。", err=True)

    result = diag_scan.scan(limit=limit, llm=llm)
    totals = result["totals"]
    click.echo(f"会话数: {totals['sessions']} | 故障: {totals['total_faults']} | "
               f"平均成功率: {totals['avg_success_rate']:.1%}")

    for kind, cnt in sorted(totals["fault_kinds"].items(), key=lambda kv: -kv[1]):
        click.echo(f"  - {kind}: {cnt}")
    for kind, cnt in sorted(totals["semantic_issues"].items(), key=lambda kv: -kv[1]):
        click.echo(f"  - [语义] {kind}: {cnt}")

    for s in result["sessions"][-10:]:
        if s.get("faults") or s.get("semantic_issues"):
            click.echo(f"\n{s.get('log_file')}  trace={s.get('trace_id', '')} "
                       f"(成功率 {s.get('success_rate', 0):.0%})")
            for ft in s.get("fault_texts", []):
                click.echo(f"    ⚠ {ft}")
            for it in s.get("semantic_issues", []):
                click.echo(f"    ✗ {it.get('type')}: {it.get('detail', '')}")

    if write_report:
        out = diag_report.write_report(result)
        click.echo(f"\n报告已生成:")
        click.echo(f"  MD : {out['md_path']}")
        click.echo(f"  JSON: {out['json_path']}")


@main.group()
def plugin():
    """能力插件管理（可插拔子系统）：list / enable / disable / uninstall."""


@plugin.command("list")
def plugin_list():
    """列出所有插件及其启停状态."""
    from research_agent.tools import get_registry, is_plugin_enabled
    from research_agent.tools.builtin import register_builtins
    register_builtins()
    reg = get_registry()
    for pid, p in sorted(reg.plugins.items()):
        state = "core" if p.is_core else ("enabled" if p.enabled else "disabled")
        tools = ",".join(t.name for t in p.tools)
        click.echo(f"  {pid:<14} [{state:<8}] {p.label or pid}  ({tools})")


@plugin.command("enable")
@click.argument("plugin_id")
def plugin_enable(plugin_id: str):
    """启用一个插件（运行时即时生效，持久化到 config）。"""
    from research_agent.tools import get_registry
    reg = get_registry()
    if reg.get_plugin(plugin_id) is None:
        click.echo(f"未知插件: {plugin_id}（先运行 plugin list）", err=True); return
    ok = reg.enable_plugin(plugin_id)
    click.echo("已启用" if ok else "启用失败", err=not ok)


@plugin.command("disable")
@click.argument("plugin_id")
def plugin_disable(plugin_id: str):
    """禁用一个插件（运行时即时注销工具，持久化到 config）。"""
    from research_agent.tools import get_registry
    reg = get_registry()
    p = reg.get_plugin(plugin_id)
    if p is None:
        click.echo(f"未知插件: {plugin_id}（先运行 plugin list）", err=True); return
    if p.is_core:
        click.echo(f"{plugin_id} 是 core 插件，不可禁用。", err=True); return
    ok = reg.disable_plugin(plugin_id)
    click.echo("已禁用" if ok else "禁用失败", err=not ok)


@plugin.command("uninstall")
@click.argument("plugin_id")
@click.option("--yes", is_flag=True, default=False, help="跳过确认")
def plugin_uninstall(plugin_id: str, yes: bool):
    """卸载插件：注销工具 + 关 config + 删除其 owned 文件/测试/数据目录."""
    from research_agent.tools import get_registry
    from research_agent.config import get_data_dir
    import os

    reg = get_registry()
    p = reg.get_plugin(plugin_id)
    if p is None:
        click.echo(f"未知插件: {plugin_id}（先运行 plugin list）", err=True); return
    if p.is_core:
        click.echo(f"{plugin_id} 是 core 插件，不可卸载。", err=True); return

    if not yes:
        owned = list(p.owned_files) + list(p.owned_packages)
        click.echo(f"将卸载插件 {plugin_id}，删除以下内容:")
        for x in owned:
            click.echo(f"  - {x}")
        for x in p.test_files:
            click.echo(f"  - {x} (test)")
        for x in p.data_dirs:
            click.echo(f"  - data_dir/{x}")
        if not click.confirm("继续?"):
            click.echo("已取消"); return

    try:
        reg.uninstall_plugin(plugin_id)
    except ValueError as e:
        click.echo(f"无法卸载: {e}", err=True); return

    src_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    data_root = str(get_data_dir())
    deleted = reg.remove_plugin_disk(p, src_root, data_root)
    click.echo(f"已卸载 {plugin_id}（注销工具 + 删除 {len(deleted)} 项磁盘内容）。")
    for d in deleted:
        click.echo(f"  - {d}")


@main.command("quality")
@click.option("--tests", default="tests/", help="测试路径 (default tests/)")
@click.option("--no-report", is_flag=True, default=False, help="不写 JSON/HTML 产物")
@click.option("--open", "open_report", is_flag=True, default=False, help="生成后用浏览器打开看板")
@click.option("--no-fail", is_flag=True, default=False, help="门禁失败也返回 0")
def quality_cmd(tests: str, no_report: bool, open_report: bool, no_fail: bool):
    """内部质量门禁：测试 / 覆盖率 / 安全 一次性评估并生成看板."""
    from research_agent.quality import gate
    report = gate.run(workdir=".", tests_path=tests)

    click.echo(f"质量门禁: {report['overall'].upper()}  "
               f"({report['summary']['passed']}/{report['summary']['total']} checks)")
    for c in report["checks"]:
        mark = "PASS" if c["passed"] else "FAIL"
        val = "n/a" if c["value"] is None else f"{c['value']}{c.get('unit', '')}"
        click.echo(f"  [{mark}] {c['label']}: {val}  {c['detail']}")

    if not no_report:
        from research_agent.quality.report import write_report
        paths = write_report(report)
        click.echo(f"  看板: {paths['html_path']}")
        if open_report:
            import webbrowser
            webbrowser.open("file:///" + paths["html_path"].replace("\\", "/"))

    if report["overall"] != "pass" and not no_fail:
        raise SystemExit(1)


@main.command("web-doctor")
def web_doctor_cmd():
    """联网搜索后端体检（借鉴 agent-reach doctor）：按路由顺序报告可用性."""
    from research_agent.tools.builtin import network
    rows = network.web_doctor()
    if not rows:
        click.echo("未配置搜索后端。设置 web.search_providers，如 [duckduckgo] 或 [exa, tavily, duckduckgo]。")
        return
    for r in rows:
        mark = "OK " if r["ready"] else "NG "
        click.echo(f"  [{mark}] {r['provider']:<11} {r['detail']}")


@main.command("audit")
@click.option("--limit", default=20, help="最近扫描的会话数 (default 20)")
@click.option("--report", "write", is_flag=True, default=False,
              help="生成 audit-{ts}.md/.json 到 data_dir/audit/")
def audit_cmd(limit: int, write: bool):
    """全链路审查评分报告：追踪 → 审查 → 评分（评的是框架有效性）."""
    from research_agent.diagnostics import audit as audit_mod
    result = audit_mod.audit(limit=limit)
    t = result["totals"]
    click.echo(f"框架总分: {t['overall']}/100 ({t['grade']})  "
               f"会话 {t['sessions']} | 工具 {t['tool_calls']}/失败 {t['tool_errors']} | "
               f"故障 {t['faults']} | 完成 {t['completed']}")
    for d, v in t["dimensions"].items():
        click.echo(f"  {d}: {v}")
    for k, c in sorted(result["issues"].items(), key=lambda kv: -kv[1]):
        click.echo(f"  ⚠ {k}: {c}")
    if write:
        out = audit_mod.write_report(result)
        click.echo(f"报告: {out['md_path']}")


@main.group()
def evolve():
    """自进化：项目经验报告 / 用户技能 / 全链路记录查询."""


@evolve.command("list")
@click.option("--target", default="", help="过滤 target: skill/install_mcp/memory/...")
@click.option("--status", default="", help="过滤 status: applied/rejected")
@click.option("--text", default="", help="文本过滤")
def evolve_list(target: str, status: str, text: str):
    """查询自进化记录（全链路审计）。"""
    from research_agent import evolve as ev
    recs = ev.query_records(target or None, status or None, text)
    if not recs:
        click.echo("（无记录）")
        return
    for r in recs:
        click.echo(f"  [{r.get('status')}] {r.get('target')} "
                   f"{r.get('artifact') or ''} entry={r.get('entry_id')} {r.get('ts')}")


@evolve.command("report")
@click.option("--out", default="", help="输出目录（默认 data_dir/evolution）")
def evolve_report(out: str):
    """生成自进化报告（md/json）并打印。"""
    from research_agent import evolve as ev
    res = ev.write_report(out or None)
    click.echo(res["markdown"])
    click.echo(f"\n报告已写入: {res['md_path']}")


@evolve.command("skills")
def evolve_skills():
    """列出用户技能及其开关状态（data_dir/skills）。"""
    from research_agent import evolve as ev
    skills = ev.list_skills()
    if not skills:
        click.echo("（无用户技能）")
        return
    for s in skills:
        state = "on " if s["enabled"] else "off"
        click.echo(f"  [{state}] {s['file']} (v{s['version']}, used {s['used']})")


@evolve.command("enable")
@click.argument("name")
def evolve_enable(name: str):
    """启用某个用户技能（写在文件 YAML 的 enabled 字段）。"""
    from research_agent import evolve as ev
    try:
        ev.set_skill_enabled(name, True)
    except FileNotFoundError:
        click.echo(f"未找到技能: {name}", err=True)
        raise SystemExit(1)
    click.echo(f"已启用技能: {name}")


@evolve.command("disable")
@click.argument("name")
def evolve_disable(name: str):
    """禁用某个用户技能（不再按其 trigger 注入）。"""
    from research_agent import evolve as ev
    try:
        ev.set_skill_enabled(name, False)
    except FileNotFoundError:
        click.echo(f"未找到技能: {name}", err=True)
        raise SystemExit(1)
    click.echo(f"已禁用技能: {name}")


@evolve.command("usage")
def evolve_usage():
    """用户技能使用统计 + 效用 A/B（开/关该技能的 telemetry 对比）。"""
    from research_agent import evolve as ev
    usage = ev.skill_usage()
    click.echo("技能使用次数:")
    if not usage:
        click.echo("  （无）")
    for name, n in sorted(usage.items(), key=lambda kv: -kv[1]):
        click.echo(f"  {name}: {n}")
    rep = ev.utility_report()
    click.echo(f"\n效用（运行 {rep['runs']} 次，使用事件 {rep['usage_events']}）:")
    for s in rep["skills"]:
        u, b = s["used"], s["baseline"]
        click.echo(f"  [{s['skill']}] uses={s['uses']} | "
                   f"tokens {u['tokens']} vs 基线 {b['tokens']} | "
                   f"cost ${u['cost_usd']} vs ${b['cost_usd']} | "
                   f"wall {u['wall_ms']} vs {b['wall_ms']} ms | "
                   f"完成 {u['completed']} vs {b['completed']}")
    p = ev.prune()
    click.echo(f"\n未使用技能（可淘汰）: {p['unused'] or '无'} (共 {p['total_skills']} 个)")


@evolve.command("experience")
@click.option("--section", default="", help="progress/decision/pitfall/procedure/tool_candidate")
@click.option("--workspace", default="", help="工作区（默认 data_dir/workspaces/default）")
def evolve_experience(section: str, workspace: str):
    """打印项目经验报告。"""
    import os
    from research_agent import report as report_mod
    from research_agent.config import get_data_dir
    ws = workspace or str(get_data_dir() / "workspaces" / "default")
    os.makedirs(ws, exist_ok=True)
    click.echo(report_mod.render_markdown(ws))
    click.echo(f"\n({len(report_mod.list_entries(ws, section=section or None))} entries)")


if __name__ == "__main__":
    main()
