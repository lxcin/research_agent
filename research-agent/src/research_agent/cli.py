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
    from research_agent.git_maintenance import resume
    resume()
    from research_agent.checkpoint_events import resume as resume_snapshot_events
    resume_snapshot_events()
    if ctx.invoked_subcommand is None:
        ctx.invoke(chat_cmd)


@main.command("sandbox-reconcile")
def sandbox_reconcile():
    """Remove this installation's orphaned containers; skip live hosts."""
    from research_agent.docker_ownership import reconcile_containers
    from research_agent.sandbox import get_sandbox_config
    import json
    try:
        removed = reconcile_containers(get_sandbox_config())
    except (RuntimeError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({"removed": removed}))


@main.group("tasks")
def tasks_cmd():
    """Inspect/cancel background tasks without entering an Agent request."""


@main.group("git-maintenance")
def git_maintenance_cmd():
    """Request or inspect asynchronous Git object maintenance."""


@git_maintenance_cmd.command("status")
def git_maintenance_status():
    """Inspect durable Git maintenance jobs without running GC in this process."""
    import json
    from research_agent.git_maintenance import status
    click.echo(json.dumps(status(), ensure_ascii=False, indent=2))


@git_maintenance_cmd.command("request")
@click.option("--workspace", required=True, type=click.Path(exists=True, file_okay=False))
def git_maintenance_request(workspace):
    """Queue conservative background GC; also retries a failed repository job."""
    import json
    from research_agent.git_maintenance import enqueue
    result = enqueue(workspace)
    if not result["success"]:
        raise click.ClickException(result["reason"])
    click.echo(json.dumps(result, ensure_ascii=False))


@tasks_cmd.command("list")
@click.option("--chat", required=True)
def tasks_list(chat):
    import json
    from research_agent.tools import get_registry
    click.echo(json.dumps(get_registry().background_tasks.list(chat), ensure_ascii=False, indent=2))


@tasks_cmd.command("cancel")
@click.option("--chat", required=True)
@click.option("--task", required=True)
def tasks_cancel(chat, task):
    from research_agent.tools import get_registry
    if not get_registry().background_tasks.cancel(chat=chat, task_id=task):
        raise click.ClickException("No active task in this session")
    click.echo("Cancellation requested; query the task to confirm cleanup completed.")


def _close_cli_session(chat):
    from research_agent.tools import get_registry
    try:
        get_registry().close_session(chat, timeout=10)
        return True
    except (TimeoutError, RuntimeError) as exc:
        click.echo(f"会话尚未完成清理：{exc}", err=True)
        return False


@main.group("evaluation")
def evaluation_cmd():
    """List task cases or explicitly run a model/strategy benchmark."""


def _evaluation_cases():
    from research_agent.tools import get_registry
    from research_agent.tools.builtin import register_builtins
    register_builtins()
    return get_registry().evaluations.cases()


@evaluation_cmd.command("list")
def evaluation_list():
    """List active plugin/tool task cases without calling a model."""
    for entry in _evaluation_cases():
        click.echo(f"{entry.key}\tversion={entry.case.version}\tchecks={len(entry.case.checks)}")


@evaluation_cmd.command("run")
@click.option("--case", "case_keys", multiple=True, help="Owner/case id; repeat to select cases. Default: all active cases.")
@click.option("--strategy", "strategy_ids", multiple=True, type=click.Choice(["function_calling", "direct_answer"]),
              help="Built-in strategy. With no strategy options, compare function_calling and direct_answer.")
@click.option("--strategy-factory", "strategy_factories", multiple=True,
              help="Explicit extension module:callable, invoked as callable(model=...) -> StrategySpec.")
@click.option("--repeats", type=click.IntRange(min=1), default=1, show_default=True)
@click.option("--model", default=None, help="Use this model for every strategy; otherwise use configured model.")
@click.option("--output", required=True, type=click.Path(dir_okay=False), help="JSON/Markdown report basename.")
@click.option("--gate", type=click.Choice(["primary", "all", "report-only"]), default="primary", show_default=True,
              help="Which strategies must pass. Primary is the first selected strategy.")
def evaluation_run(case_keys, strategy_ids, strategy_factories, repeats, model, output, gate):
    """Run isolated tasks. This command explicitly invokes the configured model."""
    from research_agent.kernel import FunctionCallingKernel
    from research_agent.evaluation_runtime import DirectAnswerKernel, runtime_strategy
    from research_agent.task_evaluation import StrategySpec, run_benchmark, write_benchmark_report
    cases = _evaluation_cases()
    selected = set(case_keys)
    unknown = selected - {entry.key for entry in cases}
    if unknown:
        raise click.ClickException(f"Unknown task cases: {sorted(unknown)}")
    if selected:
        cases = tuple(entry for entry in cases if entry.key in selected)
    if not cases:
        raise click.ClickException("No active task cases")
    if len(set(strategy_ids)) != len(strategy_ids):
        raise click.ClickException("Duplicate strategy id")
    actual_model = model or get_model_config().get("name", "openai/deepseek-chat")
    if not strategy_ids and not strategy_factories:
        strategy_ids = ("function_calling", "direct_answer")
    kernels = {"function_calling": FunctionCallingKernel, "direct_answer": DirectAnswerKernel}
    strategies = [runtime_strategy(kernels[key](), lambda: LiteLLMProvider(model=actual_model),
                    metadata={"model": actual_model, "tools": "filesystem", "host": "RuntimeHost"}) for key in strategy_ids]
    for reference in strategy_factories:
        import importlib
        try:
            module, name = reference.split(":")
            factory = getattr(importlib.import_module(module), name)
            strategy = factory(model=actual_model)
            if not isinstance(strategy, StrategySpec):
                raise TypeError("Factory must return StrategySpec")
            strategies.append(strategy)
        except Exception as exc:
            raise click.ClickException(f"Cannot load strategy factory {reference}: {exc}") from exc
    if len({strategy.id for strategy in strategies}) != len(strategies):
        raise click.ClickException("Duplicate strategy id")
    report = run_benchmark(cases, strategies, repeats=repeats)
    outputs = write_benchmark_report(report, output)
    for name, summary in report["summary"].items():
        click.echo(f"{name}: {summary['pass']}/{summary['total']} passed; failures={summary['fail']}, errors={summary['error']}, skipped={summary['skip']}")
    for path in outputs:
        click.echo(str(path.resolve()))
    # Useful as an explicit acceptance gate; an incomplete run never silently passes.
    gated = (tuple(report["summary"].values()) if gate == "all" else
             (report["summary"][strategies[0].id],) if gate == "primary" else ())
    if any(summary["fail"] or summary["error"] or summary["skip"] for summary in gated):
        raise click.exceptions.Exit(1)


def _stage_attach(path: str, chat_id: str = "default") -> list[dict]:
    from research_agent import attachments
    return attachments.parse_file(path, chat_id=chat_id)


def _ensure_workspace(path: str) -> str:
    """Ensure a workspace dir exists (init project + git), open a new CLI chat.

    Returns the new chat_id.
    """
    from research_agent import project_manager as pm
    os.makedirs(path, exist_ok=True)
    if not pm.is_project_dir(path):
        label = os.path.basename(os.path.normpath(path)) or "default"
        pm.init_project(path, topic=label)
        try:
            from research_agent.tools.git_tool import git_init
            git_init(path)
        except Exception:
            pass
    return pm.create_chat(path, title="CLI 会话")


@main.command()
@click.option("--model", default=None, help="LLM model name (e.g. deepseek/deepseek-chat)")
@click.option("--api-key", default=None, help="API key for the LLM provider")
@click.option("--api-base", default=None, help="Custom API base URL")
@click.option("--workspace", "-w", default=None,
              help="工作区目录（默认 data_dir/workspaces/default；不存在会创建）")
@click.option("--attach", "attach", multiple=True, type=click.Path(),
              help="随本轮附带的文件/图片路径（可多次；支持代码/PDF/图片/Office）")
@click.option("--isolate-session", is_flag=True, help="为会话创建独立 Git 分支和 worktree（试用）")
def chat_cmd(model: str | None, api_key: str | None, api_base: str | None,
             workspace: str | None, attach, isolate_session=False):
    """Start interactive chat session."""
    key = api_key or get_api_key()
    if not key:
        click.echo("Error: No API key configured. Use --api-key or set ANTHROPIC_API_KEY.", err=True)
        sys.exit(1)

    model_name = model or get_model_config().get("name", "deepseek/deepseek-chat")
    click.echo(f"PaperPilot CLI (model: {model_name})")
    click.echo("命令: /exit 退出 · /new 新会话 · /workspace <目录> 新建/切换工作区 · "
               "/where 当前位置 · /attach <路径> 附带文件 · /tasks 后台任务 · /cancel <任务ID> 取消\n")

    from research_agent.config import get_data_dir
    from research_agent import project_manager as pm
    if workspace:
        workspace = os.path.abspath(os.path.expanduser(workspace))
    else:
        workspace = str(get_data_dir() / "workspaces" / "default")
    chat_id = _ensure_workspace(workspace)
    state = AgentState(workspace_dir=workspace, active_chat_id=chat_id)
    click.echo(f"工作区: {workspace}\n")
    pending_blocks: list[dict] = []
    for p in attach:
        try:
            pending_blocks += _stage_attach(p)
            click.echo(f"已附加: {p}")
        except Exception as e:
            click.echo(f"附加失败 {p}: {e}", err=True)

    try:
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
                if not _close_cli_session(chat_id):
                    continue
                chat_id = pm.create_chat(workspace, title="CLI 会话")
                state = AgentState(workspace_dir=workspace, active_chat_id=chat_id)
                pending_blocks = []
                click.echo("Starting a new conversation.\n")
                continue
            if user_input.lower() in ("/where", "/pwd"):
                click.echo(f"项目目录: {workspace}\n执行目录: {state.workspace_dir}\n会话: {chat_id}\n分支: {state.session_branch or '(共享目录)'}\n")
                continue
            if user_input.lower().startswith("/workspace") or user_input.lower().startswith("/ws "):
                path = user_input.split(" ", 1)[1].strip().strip('"') if " " in user_input else ""
                if not path:
                    click.echo("用法: /workspace <目录路径>（不存在会创建）\n")
                    continue
                if not _close_cli_session(chat_id):
                    continue
                workspace = os.path.abspath(os.path.expanduser(path))
                chat_id = _ensure_workspace(workspace)
                state = AgentState(workspace_dir=workspace, active_chat_id=chat_id)
                pending_blocks = []
                click.echo(f"已切换工作区: {workspace}（新会话）\n")
                continue
            if user_input.lower() == "/tasks":
                import json
                from research_agent.tools import get_registry
                click.echo(json.dumps(get_registry().background_tasks.list(chat_id), ensure_ascii=False, indent=2))
                continue
            if user_input.lower().startswith("/cancel "):
                from research_agent.tools import get_registry
                task_id = user_input.split(" ", 1)[1].strip()
                ids = get_registry().background_tasks.cancel(chat=chat_id, task_id=task_id)
                click.echo("Cancellation requested" if ids else "No active task in this session")
                continue
            if user_input.lower().startswith("/attach "):
                p = user_input[len("/attach "):].strip().strip('"')
                try:
                    pending_blocks += _stage_attach(p)
                    click.echo(f"已附加: {p}（随下一条消息发送）")
                except Exception as e:
                    click.echo(f"附加失败: {e}", err=True)
                continue
            if not user_input:
                continue

            click.echo()
            llm = LiteLLMProvider(model=model_name, api_key=key, api_base=api_base)
            from research_agent.agent import run_agent_resilient as run_agent
            state.attachment_blocks = pending_blocks
            try:
                result = run_agent(user_input, llm, state, workspace_dir=workspace, chat_id=chat_id,
                                   isolate_session=True if isolate_session else None)
            except (RuntimeError, ValueError) as exc:
                click.echo(f"请求未开始: {exc}", err=True)
                continue
            pending_blocks = []
            if result.termination_reason == "waiting_network":
                click.echo(f"网络仍不可用，进度已保存。恢复：research-agent execution resume {result._execution_id}")
            else:
                click.echo(result.final_response or "(no response)")
            _handle_proposals(result)
            click.echo()

    finally:
        _close_cli_session(chat_id)


@main.group("session-workspace")
def session_workspace_cmd():
    """Inspect or explicitly integrate an isolated session branch."""


@session_workspace_cmd.command("info")
@click.option("--project", required=True, type=click.Path(exists=True, file_okay=False))
@click.option("--chat", required=True)
def session_workspace_info(project, chat):
    from dataclasses import asdict
    import json
    from research_agent.session_workspace import inspect_session, git
    try:
        binding = inspect_session(project, chat)
        if binding is None:
            raise ValueError("Unknown isolated session")
        data = asdict(binding)
        data.update(project_head=git(binding.project, "rev-parse", "HEAD"),
                    session_head=git(binding.project, "rev-parse", "refs/heads/" + binding.branch) if binding.archived else git(binding.workspace, "rev-parse", "HEAD"))
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
    except (RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@session_workspace_cmd.command("create")
@click.option("--project", required=True, type=click.Path(exists=True, file_okay=False))
@click.option("--chat", required=True)
def session_workspace_create(project, chat):
    """Create/reuse a bound workspace without invoking a model."""
    import json
    from dataclasses import asdict
    from research_agent.session_workspace import prepare_session
    try:
        click.echo(json.dumps(asdict(prepare_session(project, chat)), ensure_ascii=False, indent=2))
    except (RuntimeError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


@session_workspace_cmd.command("recover-info")
@click.option("--project", required=True, type=click.Path(exists=True, file_okay=False))
@click.option("--chat", required=True)
def session_workspace_recover_info(project, chat):
    """Inspect interrupted operation evidence and obtain a review token."""
    import json
    from research_agent.workspace_recovery import inspect_recovery
    try:
        click.echo(json.dumps(inspect_recovery(project, chat), ensure_ascii=False, indent=2))
    except (RuntimeError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


@session_workspace_cmd.command("recover")
@click.option("--project", required=True, type=click.Path(exists=True, file_okay=False))
@click.option("--chat", required=True)
@click.option("--decision", required=True, type=click.Choice(["complete", "cancel"]))
@click.option("--review-token", required=True)
def session_workspace_recover(project, chat, decision, review_token):
    """Apply an explicitly reviewed recovery action."""
    import json
    from research_agent.workspace_recovery import recover_session
    try:
        click.echo(json.dumps(recover_session(project, chat, decision=decision, review_token=review_token),
                              ensure_ascii=False, indent=2))
    except (RuntimeError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


@main.group("checkpoint")
def checkpoint_cmd():
    """Review and restore workspace snapshots, preserving the original index."""


@checkpoint_cmd.command("list")
@click.option("--workspace", required=True, type=click.Path(exists=True, file_okay=False))
def checkpoint_list(workspace):
    import json
    from research_agent.checkpoint import list_checkpoints
    click.echo(json.dumps(list_checkpoints(workspace), ensure_ascii=False, indent=2))


@checkpoint_cmd.command("events")
@click.option("--workspace", required=True, type=click.Path(exists=True, file_okay=False))
def checkpoint_events(workspace):
    """Inspect snapshot completion projection and recovery blockers."""
    import json
    from research_agent.checkpoint_events import status
    click.echo(json.dumps(status(workspace), ensure_ascii=False, indent=2))


@checkpoint_cmd.command("review")
@click.option("--workspace", required=True, type=click.Path(exists=True, file_okay=False))
@click.option("--ref", "ref", required=True)
def checkpoint_review(workspace, ref):
    import json
    from research_agent.checkpoint import review_checkpoint
    result = review_checkpoint(workspace, ref)
    if not result["success"]:
        raise click.ClickException(json.dumps(result, ensure_ascii=False))
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@checkpoint_cmd.command("restore")
@click.option("--workspace", required=True, type=click.Path(exists=True, file_okay=False))
@click.option("--ref", "ref", required=True)
@click.option("--review-token", required=True)
def checkpoint_restore(workspace, ref, review_token):
    import json
    from research_agent.checkpoint import restore_checkpoint
    result = restore_checkpoint(workspace, ref, review_token=review_token)
    if not result["success"]:
        raise click.ClickException(json.dumps(result, ensure_ascii=False))
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@checkpoint_cmd.command("undo-review")
@click.option("--workspace", required=True, type=click.Path(exists=True, file_okay=False))
@click.option("--ref", "ref", required=True)
@click.option("--path", "paths", multiple=True, help="Select files; omit to undo all remaining turn changes.")
@click.option("--resolved", multiple=True, help="Use the edited conflict result for this file; always review again.")
def checkpoint_undo_review(workspace, ref, paths, resolved):
    import json
    from research_agent.rollback import review_undo
    result = review_undo(workspace, ref, paths or None, resolved=resolved)
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["success"]:
        raise click.ClickException(result["reason"])


@checkpoint_cmd.command("undo-apply")
@click.option("--workspace", required=True, type=click.Path(exists=True, file_okay=False))
@click.option("--review-token", required=True)
def checkpoint_undo_apply(workspace, review_token):
    import json
    from research_agent.rollback import apply_undo
    result = apply_undo(workspace, review_token=review_token)
    if not result["success"]:
        raise click.ClickException(json.dumps(result, ensure_ascii=False))
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@checkpoint_cmd.command("undo-cancel")
@click.option("--workspace", required=True, type=click.Path(exists=True, file_okay=False))
@click.option("--review-token", required=True)
def checkpoint_undo_cancel(workspace, review_token):
    import json
    from research_agent.rollback import cancel_conflict
    result = cancel_conflict(workspace, review_token=review_token)
    if not result["success"]:
        raise click.ClickException(json.dumps(result, ensure_ascii=False))
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@session_workspace_cmd.command("integrate")
@click.option("--project", required=True, type=click.Path(exists=True, file_okay=False))
@click.option("--chat", required=True)
@click.option("--expected-project-head", required=True)
@click.option("--expected-session-head", required=True)
def session_workspace_integrate(project, chat, expected_project_head, expected_session_head):
    from research_agent.session_workspace import merge_session
    try:
        result = merge_session(project, chat, expected_project_head=expected_project_head,
                               expected_session_head=expected_session_head)
        click.echo(result["head"])
    except (RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@session_workspace_cmd.command("storage")
@click.option("--project", required=True, type=click.Path(exists=True, file_okay=False))
@click.option("--chat", required=True)
def session_workspace_storage(project, chat):
    import json
    from research_agent.session_workspace import session_storage
    try:
        click.echo(json.dumps(session_storage(project, chat), ensure_ascii=False, indent=2))
    except (RuntimeError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


@session_workspace_cmd.command("archive")
@click.option("--project", required=True, type=click.Path(exists=True, file_okay=False))
@click.option("--chat", required=True)
@click.option("--expected-project-head", required=True)
@click.option("--expected-session-head", required=True)
def session_workspace_archive(project, chat, expected_project_head, expected_session_head):
    from research_agent.session_workspace import archive_session
    try:
        result = archive_session(project, chat, expected_project_head=expected_project_head,
                                 expected_session_head=expected_session_head)
        click.echo(f"Archived {result.branch}; committed history retained")
    except (RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@session_workspace_cmd.command("restore")
@click.option("--project", required=True, type=click.Path(exists=True, file_okay=False))
@click.option("--chat", required=True)
def session_workspace_restore(project, chat):
    from research_agent.session_workspace import restore_session
    try:
        click.echo(restore_session(project, chat).workspace)
    except (RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


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
    click.echo("不可回滚：Git 忽略文件、远程推送/API/数据库副作用；敏感文件不自动备份。")
    click.echo("  命令: keep all | undo all | keep 1,3 | undo 2 | diff 2 | done")

    def _reset(state, remaining_paths):
        props = pmgr.collect(remaining_paths) if remaining_paths else []
        state.pending_proposals = props
        return props

    def _review_selected_undo(paths):
        from research_agent.review_baseline import baseline
        if not baseline(workspace, "active-turn"):
            return True  # Legacy proposals keep their strict, previously reviewed contract.
        result = pmgr.review_undo(paths)
        if not result["success"]:
            import json
            click.echo(json.dumps(result, ensure_ascii=False, indent=2))
            return False
        for item in result["files"]:
            click.echo(item["path"] + "\n" + (item["diff"] or "（内容预览不可用或仅状态改变，请核对目标摘要）"))
            if item["diff_truncated"]:
                click.echo("预览已截断；完整候选结果已按内容摘要固定。")
        return click.confirm("应用以上撤销计划？", default=False)

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
                result = pmgr.keep_all()
                if not result.get("success"):
                    click.echo("操作未执行：" + result.get("reason", "失败") + " " + str(result.get("conflicts", [])))
                    continue
                state.pending_proposals = []
                click.echo("已保留全部改动。")
                break
            indexes = [int(x) for x in targets if x.isdigit()]
            paths = [changes[i - 1].path for i in indexes if 1 <= i <= len(changes)]
            if paths:
                result = pmgr.keep(paths)
                if not result.get("success"):
                    click.echo("操作未执行：" + result.get("reason", "失败") + " " + str(result.get("conflicts", [])))
                    continue
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
                if not _review_selected_undo([c.path for c in changes]):
                    continue
                result = pmgr.undo_all(review_token=pmgr.review_token)
                if not result.get("success"):
                    click.echo("操作未执行：" + result.get("reason", "失败") + " " + str(result.get("conflicts", [])))
                    continue
                state.pending_proposals = []
                click.echo("已撤销全部改动。")
                break
            indexes = [int(x) for x in targets if x.isdigit()]
            paths = [changes[i - 1].path for i in indexes if 1 <= i <= len(changes)]
            if paths:
                if not _review_selected_undo(paths):
                    continue
                result = pmgr.undo(paths, review_token=pmgr.review_token)
                if not result.get("success"):
                    click.echo("操作未执行：" + result.get("reason", "失败") + " " + str(result.get("conflicts", [])))
                    continue
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


@main.group()
def mcp():
    """MCP 外部工具：add / list / remove / test（写入 skills/mcp.yml）。"""


@mcp.command("list")
def mcp_list():
    """列出已配置的 MCP server 与插件开关状态。"""
    from research_agent.tools import is_plugin_enabled
    from research_agent.tools import mcp_loader as M
    on = is_plugin_enabled("mcp")
    click.echo(f"mcp 插件: {'enabled' if on else 'disabled'}  (config: {M.default_config_path()})")
    servers = M.load_servers()
    if not servers:
        click.echo("（无配置 server；用 `research-agent mcp add <name> -- <cmd...>` 添加）")
        return
    for s in servers:
        cmd = " ".join(s.get("command") or []) or "(空)"
        env = s.get("env") or {}
        click.echo(f"  {M.server_key(s):<16} {cmd}" + (f"  env={list(env)}" if env else ""))


@mcp.command("add")
@click.argument("name")
@click.argument("command", nargs=-1, required=True)
@click.option("--env", "envs", multiple=True, help="环境变量 K=V（可多次）")
def mcp_add(name: str, command, envs):
    """添加/覆盖一个 MCP server。

    示例：research-agent mcp add exa -- npx -y mcporter run exa
    """
    from research_agent.tools import mcp_loader as M
    env = {}
    for kv in envs:
        if "=" in kv:
            k, v = kv.split("=", 1)
            env[k.strip()] = v
    M.add_server(name, list(command), env or None)
    click.echo(f"已添加 server '{name}': {' '.join(command)}")


@mcp.command("remove")
@click.argument("name")
def mcp_remove(name: str):
    """按名称移除一个 MCP server。"""
    from research_agent.tools import mcp_loader as M
    ok = M.remove_server(name)
    click.echo("已移除" if ok else f"未找到 server: {name}", err=not ok)


@mcp.command("test")
@click.argument("name")
def mcp_test(name: str):
    """连接该 server，列出它暴露的工具后退出。"""
    from research_agent.tools import mcp_loader as M
    entry = M.get_server(name)
    if not entry:
        click.echo(f"未找到 server: {name}", err=True)
        raise SystemExit(1)
    cmd = entry.get("command") or []
    if not cmd:
        click.echo("该 server 无 command（占位）", err=True)
        raise SystemExit(1)
    click.echo(f"连接 {name}: {' '.join(cmd)} ...")
    client = M.MCPClient(cmd, env=entry.get("env"))
    if not client.connect():
        click.echo("连接失败（检查命令/依赖/网络）", err=True)
        raise SystemExit(1)
    try:
        tools = client.list_tools()
        click.echo(f"OK：{len(tools)} 个工具")
        for t in tools:
            click.echo(f"  - {t.get('name', '')}: {str(t.get('description', ''))[:80]}")
    finally:
        client.close()


@main.group()
def attachments():
    """附件管理：list / cleanup（chat 级暂存，存 data_dir/attachments/<chat>/）。"""


@attachments.command("list")
@click.option("--chat", default="default", help="会话 id（默认 default）")
def attachments_list(chat: str):
    """列出某个会话已暂存的附件。"""
    from research_agent import attachments as A
    items = A.list_attachments(chat)
    if not items:
        click.echo("（无附件）")
        return
    for m in items:
        click.echo(f"  {m['id']}  {m['kind']:<5} {m['size']}B  {m['filename']}")


@attachments.command("cleanup")
@click.option("--chat", default=None, help="只清某个会话（默认清全部）")
def attachments_cleanup(chat: str):
    """清理暂存的附件（随会话回收）。"""
    from research_agent import attachments as A
    n = A.cleanup(chat)
    click.echo(f"已清理 {n} 个附件目录" + (f"（chat={chat}）" if chat else "（全部）"))


@main.group("execution")
def execution_group():
    """查询执行、人工核对不确定结果并恢复。"""


@execution_group.command("inspect")
@click.argument("execution_id")
def execution_inspect(execution_id):
    import json
    from research_agent.recovery import inspect_execution
    click.echo(json.dumps(inspect_execution(execution_id), ensure_ascii=False, indent=2))


@execution_group.command("reconcile")
@click.argument("execution_id")
@click.argument("call_id")
@click.option("--decision", required=True, type=click.Choice(["completed", "not_executed"]))
@click.option("--note", required=True, help="核对依据，保存到审计记录")
@click.option("--result-file", type=click.Path(exists=True), help="已确认的 ToolResult JSON")
def execution_reconcile(execution_id, call_id, decision, note, result_file):
    import json
    from research_agent.recovery import reconcile
    from research_agent.tools.schema import ToolResult
    result = None
    if result_file:
        with open(result_file, encoding="utf-8") as stream:
            result = ToolResult(**json.load(stream))
    click.echo(json.dumps(reconcile(execution_id, call_id, decision=decision, note=note, result=result), ensure_ascii=False))


@execution_group.command("resume")
@click.argument("execution_id")
def execution_resume(execution_id):
    from research_agent.recovery import inspect_execution
    from research_agent.agent import run_agent_resilient as run_agent
    info = inspect_execution(execution_id)
    result = run_agent(info["message"], LiteLLMProvider(), AgentState(),
                       workspace_dir=info["workspace"], chat_id=info["chat_id"], execution_id=execution_id)
    click.echo(result.final_response or result.error or
               (f"进度已保存，等待网络恢复：{execution_id}" if result.termination_reason == "waiting_network" else ""))
    if result.error:
        raise click.ClickException(result.error)


if __name__ == "__main__":
    main()
