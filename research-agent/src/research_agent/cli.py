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


@main.command()
@click.option("--project", default=None, help="项目ID")
@click.option("--limit", default=20, help="显示条数")
def history(project: str, limit: int):
    """查看对话历史"""
    from research_agent.memory import get_all_turns, get_recent_turns
    from research_agent.store import init_db, get_all_projects

    init_db()
    if project:
        turns = get_recent_turns(project, limit=limit)
        for t in turns:
            if t.compressed:
                click.echo(f"  ── 已压缩 ── 摘要: {t.summary[:100]}")
            else:
                click.echo(f"  [{t.timestamp[:16]}] 用户: {t.user_message}")
                if t.assistant_message:
                    click.echo(f"  [{t.timestamp[:16]}] 助手: {t.assistant_message[:200]}")
    else:
        projects = get_all_projects()
        for p in projects:
            click.echo(f"\n项目: {p.topic}")
            turns = get_recent_turns(p.id, limit=5)
            for t in turns:
                if t.compressed:
                    click.echo(f"  ── 已压缩 ── 摘要: {t.summary[:100]}")
                else:
                    click.echo(f"  [{t.timestamp[:16]}] 用户: {t.user_message[:60]}")
                    if t.assistant_message:
                        click.echo(f"  [{t.timestamp[:16]}] 助手: {t.assistant_message[:60]}")


@main.command()
@click.option("--paper", default=None, help="论文ID或标题")
def graph(paper: str):
    """查看知识图谱"""
    from research_agent.knowledge_graph import load_graph, build_paper_argument_tree
    from research_agent.llm import LiteLLMProvider
    from research_agent.store import get_all_papers, init_db

    init_db()
    if paper:
        papers = get_all_papers()
        target = None
        for p in papers:
            if paper.lower() in p.title.lower() or paper == p.id:
                target = p
                break
        if not target:
            click.echo(f"论文 '{paper}' 未找到")
            return
        from research_agent.ingestion import recall_full_paper
        text = recall_full_paper(target.id)
        llm = LiteLLMProvider()
        tree = build_paper_argument_tree(target.id, text, llm)
        _print_tree(tree["tree"])
    else:
        kg = load_graph()
        click.echo(f"全局图谱: {len(kg.graph.nodes)} 个观点, {len(kg.graph.edges)} 条关系")
        for node_id in kg.graph.nodes:
            node = kg.graph.nodes[node_id]
            claim = node["claim"]
            click.echo(f"  [{claim.claim_type}] {claim.text[:80]}")


def _print_tree(node, indent=0):
    prefix = "  " * indent
    if "thesis" in node:
        click.echo(f"{prefix}核心论点: {node['thesis']}")
    if "text" in node:
        click.echo(f"{prefix}├── [{node.get('type', 'claim')}] {node['text']}")
    for child in node.get("children", []):
        _print_tree(child, indent + 1)


@main.command()
@click.argument("project_id")
def review(project_id: str):
    """复盘项目进度"""
    from research_agent.store import get_project
    from research_agent.progress import review_project
    from research_agent.llm import LiteLLMProvider

    project = get_project(project_id)
    if not project:
        click.echo(f"项目 {project_id} 不存在")
        return

    llm = LiteLLMProvider()
    project = review_project(project, llm)
    click.echo(f"项目: {project.topic}")
    click.echo(f"进度: {project.history_summary}")
    click.echo(f"更新时间: {project.updated_at}")


if __name__ == "__main__":
    main()