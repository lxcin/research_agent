"""Host admission and durable suspension; file undo is not an execution cursor."""
import json

from research_agent.execution import RecoveryRequired, checkpoint, restore_execution_state, serializable_caps, workspace_digest


def identity(state, user_input, workspace, chat):
    return json.dumps([user_input, workspace, chat, state._kernel_id])


def prepare_execution(ctx, kernel_id):
    """Resume the same execution before any new baseline can retire its S."""
    from research_agent import checkpoint as cp
    from research_agent.review_baseline import baseline
    from research_agent.checkpoint_events import register_run
    state, workspace = ctx.state, ctx.workspace_dir
    state._kernel_id = kernel_id
    state._replace_partial_reply = False
    key = identity(state, ctx.user_input, workspace, ctx.chat_id)
    cp.recover_snapshot_transition(workspace)
    saved = checkpoint(state._execution_id, key)
    if not saved:
        cp.snapshot_for_run(state, workspace, label="run")
        return
    from research_agent.tools import get_registry
    if get_registry().background_tasks.workspace_pending(workspace):
        raise RecoveryRequired("Background writers must be drained before resuming")
    journal = baseline(workspace, "checkpoint-rollback")
    if journal.get("status") in {"applying", "partial"}:
        raise RecoveryRequired("Complete the partial rollback before resuming")
    ref = saved.get("workspace_checkpoint", "")
    if ref:
        try:
            sha = cp._resolve_owned(workspace, ref)
            if baseline(workspace, "active-turn").get("sha") != sha:
                raise ValueError("A newer turn owns this workspace")
        except (ValueError, RuntimeError) as exc:
            saved["interruption"] = {"status": "superseded", "reason": "Execution baseline retired by a newer turn"}
            checkpoint(state._execution_id, key, saved)
            raise RecoveryRequired("Execution baseline retired; start a new task from the current workspace") from exc
    # Check plugin migrations before retiring any evidence or running a model.
    restore_execution_state(state, saved)
    state._last_checkpoint = ref
    if "final" not in saved:
        current = cp._state(workspace) if cp.is_repo(workspace) else None
        previous = saved.get("pause_workspace", saved.get("workspace_state"))
        previous_digest = workspace_digest(previous)
        if saved.get("pending"):
            from research_agent.execution import completed_call_result
            # The tool may have committed its result just before a host crash,
            # while the loop cursor still points to it. Its fixed completion
            # evidence accounts for that change without running it again.
            for call in saved["pending"]:
                result = completed_call_result(state._execution_id, call["id"], call["name"], call["params"])
                if result is None:
                    break
                previous_digest = result.get("workspace_digest", workspace_digest(result["workspace_state"])
                    if "workspace_state" in result else previous_digest)
        changed = workspace_digest(current) != previous_digest
        # An already sealed E is immutable. Legacy resumes therefore start a
        # fresh execution segment rather than widening an approved old E.
        sealed = bool(ref and baseline(workspace, "checkpoint:" + sha))
        if changed or sealed:
            if saved.get("pending"):
                raise RecoveryRequired("Workspace changed with pending tool intent; reconcile it before resuming")
            saved.setdefault("messages", []).append({"role": "system", "content":
                "[执行恢复] 工作区可能已变化。此前已完成工具的结果保留为历史证据；"
                "继续写入或判断任务完成前，重新读取相关文件核实当前内容，不覆盖人工修改。"})
            saved["phase"] = "next"
            saved["workspace_state"] = current
            # The new cursor must commit before old Git references retire.
            # A pinned intent lets admission finish either side after a crash.
            def bind_snapshot(new_ref):
                saved["workspace_checkpoint"] = new_ref
                saved["pause_workspace"] = current
                checkpoint(state._execution_id, key, saved)
            cp.snapshot_for_run(state, workspace, label="resume-segment", on_created=bind_snapshot)
        elif ref:
            register_run(state, workspace, ref, sha)
        state._replace_partial_reply = bool(saved.get("interruption", {}).get("partial"))
        saved.pop("interruption", None)
        saved.pop("pause_workspace", None)
        checkpoint(state._execution_id, key, saved)
    state.error = ""
    state.final_response = ""
    state.termination_reason = ""
    if ctx.emit:
        ctx.emit("execution_resumed", {"execution_id": state._execution_id,
                 "phase": saved.get("phase"), "rounds": saved.get("rounds", 0)})


def suspend_execution(state, user_input, workspace, chat, cause, *, partial=""):
    """Persist the last complete step, never partial model tool arguments."""
    from research_agent import checkpoint as cp
    from research_agent.tools import get_registry
    key = identity(state, user_input, workspace, chat)
    saved = checkpoint(state._execution_id, key)
    if not saved:
        raise RecoveryRequired("This kernel has no durable step checkpoint; automatic resume is unavailable")
    saved["interruption"] = {"status": "waiting_network", "reason": str(cause), "partial": partial}
    saved["caps"] = serializable_caps(state.caps)
    saved["fields"] = state.components.snapshot()
    # If background writers remain, their eventual state must be re-read on
    # admission. It is not a trustworthy fixed point yet.
    saved["pause_workspace"] = (cp._state(workspace) if cp.is_repo(workspace)
        and not get_registry().background_tasks.workspace_pending(workspace) else None)
    checkpoint(state._execution_id, key, saved)
