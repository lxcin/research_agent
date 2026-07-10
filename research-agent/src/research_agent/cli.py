"""CLI entry point for research-agent using Click."""
import sys
import click

from research_agent.agent import process_user_input, AgentState
from research_agent.store import init_db
from research_agent.config import get_data_dir


@click.group()
def main():
    """Research Agent - your persistent research partner."""
    pass


@main.command()
@click.argument("message", required=False)
@click.option("--project", "-p", default=None, help="Specify project ID to resume")
def chat(message, project):
    """Start an interactive chat session or send a single message."""
    init_db()

    if message:
        state = AgentState(user_input=message)
        result = process_user_input(state)
        click.echo(f"\n{result.final_response}")
        if result.citations:
            click.echo(f"\n引用: {', '.join(result.citations)}")
        click.echo(f"自信度: {result.confidence}")
        return

    click.echo("欢迎使用科研助手！我是您的研究伙伴 Agent。")
    click.echo("输入 'exit' 或 'quit' 退出, '/projects' 查看项目列表")
    click.echo(f"数据目录: {get_data_dir()}")

    state = AgentState()

    while True:
        try:
            user_input = click.prompt("\n> ", prompt_suffix="").strip()
        except (KeyboardInterrupt, EOFError):
            click.echo("\n再见！")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            click.echo("再见！")
            break
        if user_input == "/projects":
            from research_agent.store import get_all_projects
            projects = get_all_projects()
            if not projects:
                click.echo("还没有项目。开始对话即可自动创建项目。")
            else:
                for p in projects:
                    status_icon = {"active": "▶", "waiting": "⏸", "paused": "⏹", "done": "✓"}
                    icon = status_icon.get(p.status.value, "?")
                    pending = f" [等待: {p.pending_task.description}]" if p.pending_task else ""
                    click.echo(f"  {icon} {p.topic}{pending}")
            continue

        state.user_input = user_input
        state.retry_count = 0
        state.retrieved_chunks = []
        state.final_response = ""
        state.error = ""

        thread_id = getattr(state.active_project, "id", "default") or "default"
        result = process_user_input(state, thread_id=thread_id)
        click.echo(f"\n{result.final_response}")

        if state.active_project and state.active_project.pending_task:
            click.echo(f"\n⏸ 提醒: 项目「{state.active_project.topic}」等待您完成: {state.active_project.pending_task.description}")

        state = result


@main.command()
def status():
    """Show current agent status and project overview."""
    init_db()

    from research_agent.store import get_all_projects
    projects = get_all_projects()

    click.echo("=== 科研助手 Agent 状态 ===\n")

    if projects:
        click.echo(f"=== 项目列表 ({len(projects)}) ===")
        for p in projects:
            status_text = {"active": "进行中", "waiting": "等待用户", "paused": "已暂停", "done": "已完成"}
            st = status_text.get(p.status.value, p.status.value)
            pending = f" | 等待: {p.pending_task.description}" if p.pending_task else ""
            plan_count = len(p.plan)
            click.echo(f"  [{st}] {p.topic}{pending} (计划: {plan_count} 步)")
            if p.history_summary:
                click.echo(f"       摘要: {p.history_summary[:100]}")
    else:
        click.echo("暂无项目。开始对话即可自动创建。")


if __name__ == "__main__":
    main()