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
        click.echo()


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
def feature():
    """可选功能（可插拔子系统）管理：list / enable / disable / uninstall."""


@feature.command("list")
def feature_list():
    """列出所有功能及其启停状态."""
    from research_agent.features import list_features, is_enabled, is_core
    for f in list_features():
        state = "core" if is_core(f.id) else ("enabled" if is_enabled(f.id) else "disabled")
        click.echo(f"  {f.id:<18} [{state:<8}] {f.label}")


@feature.command("enable")
@click.argument("feature_id")
def feature_enable(feature_id: str):
    """启用一个可选功能."""
    from research_agent.features import set_enabled, is_core, get_feature
    if is_core(feature_id):
        click.echo("core 功能不可禁用。", err=True); return
    if get_feature(feature_id) is None:
        click.echo(f"未知功能: {feature_id}", err=True); return
    ok = set_enabled(feature_id, True)
    click.echo("已启用" if ok else "启用失败", err=not ok)


@feature.command("disable")
@click.argument("feature_id")
def feature_disable(feature_id: str):
    """禁用一个可选功能（接线点将跳过其逻辑）."""
    from research_agent.features import set_enabled, is_core, get_feature
    if is_core(feature_id):
        click.echo("core 功能不可禁用。", err=True); return
    if get_feature(feature_id) is None:
        click.echo(f"未知功能: {feature_id}", err=True); return
    ok = set_enabled(feature_id, False)
    click.echo("已禁用" if ok else "禁用失败", err=not ok)


@feature.command("uninstall")
@click.argument("feature_id")
@click.option("--yes", is_flag=True, default=False, help="跳过确认")
def feature_uninstall(feature_id: str, yes: bool):
    """静态卸载一个可选功能：禁用 + 删除其全部 owned 文件/测试/数据目录."""
    from research_agent.features import (get_feature, is_core, is_enabled, set_enabled,
                                         scan_references, dependents as feat_dependents)
    from research_agent.config import get_data_dir
    import os
    import shutil

    f = get_feature(feature_id)
    if f is None:
        click.echo(f"未知功能: {feature_id}", err=True); return
    if is_core(feature_id):
        click.echo(f"{feature_id} 是 core，不可卸载。", err=True); return

    src_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    offenders = scan_references(feature_id, src_root)
    if offenders:
        click.echo(f"无法卸载 {feature_id}：仍有 core 引用（请先解除接线门控）:", err=True)
        for o in offenders[:15]:
            click.echo(f"  - {o}", err=True)
        click.echo(f"  共 {len(offenders)} 处引用。", err=True)
        return

    dependents = feat_dependents(feature_id)
    if dependents:
        click.echo(f"无法卸载 {feature_id}：仍有启用的功能依赖它: {', '.join(dependents)}。"
                   f"请先卸载/禁用依赖方。", err=True)
        return

    if not yes:
        owned = f.owned_files + f.owned_packages
        click.echo(f"将卸载 {feature_id}，删除以下内容:")
        for x in owned:
            click.echo(f"  - {x}")
        for x in f.test_files:
            click.echo(f"  - {x} (test)")
        for x in f.data_dirs:
            click.echo(f"  - data_dir/{x}")
        if not click.confirm("继续?"):
            click.echo("已取消"); return

    deleted = []
    # source files
    for rel in f.owned_files:
        p = os.path.join(src_root, rel)
        if os.path.isfile(p):
            os.remove(p); deleted.append(rel)
    # owned packages (dirs/modules): research_agent.diagnostics → <src>/research_agent/diagnostics
    for pkg in f.owned_packages:
        parts = pkg.split(".")
        pkg_dir = os.path.join(src_root, *parts[1:])
        if os.path.isdir(pkg_dir):
            shutil.rmtree(pkg_dir, ignore_errors=True); deleted.append(pkg)
        elif os.path.isfile(pkg_dir + ".py"):
            os.remove(pkg_dir + ".py"); deleted.append(pkg)
    # tests
    for rel in f.test_files:
        p = os.path.join(src_root, rel)
        if os.path.isfile(p):
            os.remove(p); deleted.append(rel)
    # data dirs
    data_root = str(get_data_dir())
    for d in f.data_dirs:
        dp = os.path.join(data_root, d)
        if os.path.isdir(dp):
            shutil.rmtree(dp, ignore_errors=True); deleted.append(f"data_dir/{d}")

    set_enabled(feature_id, False)
    click.echo(f"已卸载 {feature_id} ({len(deleted)} 项)。可从 FEATURES 目录移除其声明 {feature_id}。")
    for d in deleted:
        click.echo(f"  - {d}")


if __name__ == "__main__":
    main()
