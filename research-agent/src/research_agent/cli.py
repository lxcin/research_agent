"""CLI entry point for PaperPilot research agent."""
import os
import sys

import click

from research_agent.agent import chat, AgentState
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


if __name__ == "__main__":
    main()
