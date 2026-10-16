"""Explicit, reviewed recovery of interrupted worktree operations.

Never infer a task's intent from an arbitrary leftover directory. New operations
write their complete intent before Git changes anything; legacy records support
metadata adoption or cancellation only when the evidence is sufficient.
"""
from contextlib import contextmanager
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import re

from research_agent.session_workspace import (
    SessionWorkspace, _binding_path, _paths, _publish_binding, _validate, canonical, git,
)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _binding(value, project, chat):
    binding = SessionWorkspace(**value)
    manifest, checkout, branch = _paths(project, chat)
    if (canonical(binding.project) != canonical(project) or binding.chat_id != chat
            or canonical(binding.workspace) != canonical(checkout) or binding.branch != branch
            or not isinstance(binding.archived, bool)
            or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", binding.base_commit)
            or (binding.archived and not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", binding.archived_head))):
        raise ValueError("Operation binding identity is invalid")
    if checkout.resolve().is_relative_to(Path(project).resolve()):
        raise ValueError("Recovery checkout must be outside the project")
    return binding


def _worktrees(project):
    entries, entry = [], {}
    for item in git(project, "worktree", "list", "--porcelain", "-z").split("\0"):
        if not item:
            if entry:
                entries.append(entry)
                entry = {}
            continue
        key, _, value = item.partition(" ")
        entry[key] = value
    if entry:
        entries.append(entry)
    return entries


def _branch_head(project, branch):
    # rev-parse --verify has ambiguous error codes; list the exact ref instead.
    lines = git(project, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads/" + branch)
    return next((line.split(" ", 1)[1] for line in lines.splitlines()
                 if line.startswith("refs/heads/" + branch + " ")), None)


def _inspect(project, chat):
    manifest, checkout, branch = _paths(project, chat)
    raw = _read(_binding_path(chat))
    if not raw or raw.get("status") != "preparing":
        raise ValueError("No incomplete session operation")
    if raw.get("chat_id") != chat or canonical(raw.get("project", "")) != canonical(project):
        raise ValueError("Incomplete operation belongs to another project/session")
    common = canonical(git(project, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    identity = Path(common).stat()
    entry = next((value for value in _worktrees(project)
                  if canonical(value["worktree"]) == canonical(checkout)), None)
    facts = {"intent": raw, "manifest": _read(manifest), "checkout_exists": os.path.lexists(checkout),
             "branch_head": _branch_head(project, branch), "registered_worktree": entry,
             "project_head": git(project, "rev-parse", "HEAD"),
             "project_branch": git(project, "symbolic-ref", "--short", "HEAD"),
             "repository": common, "repository_id": [identity.st_dev, identity.st_ino]}
    issues, actions, before, after = [], [], None, None
    operation = raw.get("operation", "legacy")
    try:
        if operation == "legacy":
            if facts["manifest"] is not None:
                after = _binding(facts["manifest"], project, chat)
                _validate(after)
                actions = ["complete"]
            elif not facts["checkout_exists"] and entry is None and facts["branch_head"] is None:
                actions = ["cancel"]
            else:
                issues.append("Legacy intent has no binding evidence; preserve files/branch for manual reconciliation")
        else:
            if raw.get("version") != 1 or operation not in {"create", "archive", "restore"}:
                raise ValueError("Unsupported operation contract")
            if raw.get("repository") != common or raw.get("repository_id") != facts["repository_id"]:
                raise ValueError("Project repository identity changed")
            after = _binding(raw["after"], project, chat)
            before = _binding(raw["before"], project, chat) if raw["before"] is not None else None
            if operation == "create":
                if before is not None or after.archived:
                    raise ValueError("Invalid create intent")
                expected_head = after.base_commit
            else:
                if before is None:
                    raise ValueError("Operation start binding is missing")
                expected = replace(before, archived=True, archived_head=raw["after"]["archived_head"]) if operation == "archive" else replace(before, archived=False, archived_head="")
                if after != expected or before.archived != (operation == "restore"):
                    raise ValueError("Invalid archive/restore intent")
                expected_head = after.archived_head if operation == "archive" else before.archived_head
            if facts["manifest"] not in (None, asdict(before) if before else None, asdict(after)):
                raise ValueError("Session manifest changed independently")
            if facts["project_branch"] != after.project_branch:
                raise ValueError("Project branch changed")
            if facts["branch_head"] not in (None, expected_head):
                raise ValueError("Session branch changed")
            if entry is not None:
                if not facts["checkout_exists"] or "locked" in entry or "prunable" in entry:
                    raise ValueError("Git worktree is locked or missing; explicit Git repair is required")
                if entry.get("branch") != "refs/heads/" + branch or entry.get("HEAD") != expected_head:
                    raise ValueError("Registered worktree identity changed")
                _validate(replace(after, archived=False, archived_head=""))
                facts["dirty"] = git(checkout, "status", "--porcelain", "--untracked-files=all", "--ignored")
            elif facts["checkout_exists"]:
                raise ValueError("Unregistered directory exists; refusing to overwrite it")
            if operation == "create":
                actions = ["complete"]
                if entry is None and facts["branch_head"] is None and facts["manifest"] is None:
                    actions.append("cancel")
            elif operation == "archive":
                if facts["branch_head"] != expected_head:
                    raise ValueError("Archived branch is missing")
                git(project, "merge-base", "--is-ancestor", expected_head, facts["project_head"])
                if entry is not None:
                    actions.append("cancel")
                    if not facts.get("dirty"):
                        actions.append("complete")
                else:
                    actions = ["complete"]
            elif facts["branch_head"] == expected_head:
                actions = ["complete"] + (["cancel"] if entry is None else [])
            else:
                raise ValueError("Restore branch is missing")
    except (RuntimeError, ValueError, TypeError, KeyError) as exc:
        issues.append(str(exc))
        actions = []
    token = hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    effects = {}
    if "complete" in actions:
        if operation == "archive":
            effects["complete"] = (["remove verified clean checkout; retain branch"] if entry is not None else []) + ["publish archived binding"]
        elif operation in {"create", "restore"}:
            effects["complete"] = (["create checkout from saved branch/base"] if entry is None else ["preserve existing checkout and edits"]) + ["publish active binding"]
        else:
            effects["complete"] = ["adopt validated manifest; do not modify files"]
    if "cancel" in actions:
        effects["cancel"] = ["restore previous binding; do not modify files"] if before is not None else ["remove incomplete binding marker only"]
    return {"operation": operation, "project": canonical(project), "chat_id": chat,
            "review_token": token, "actions": actions, "effects": effects, "issues": issues, "facts": facts}, before, after


@contextmanager
def _locked(project, chat):
    from research_agent.execution import exclusive_request
    from research_agent.docker_ownership import reconcile_before_request
    from research_agent.background_tasks import recover_workspace
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", chat):
        raise ValueError("Invalid session identity")
    reconcile_before_request()
    _, checkout, _ = _paths(project, chat)
    recover_workspace(project)
    recover_workspace(str(checkout))
    with exclusive_request(str(_binding_path(chat)), chat):
        with exclusive_request(project, "session-recovery-project:" + canonical(project)):
            with exclusive_request(str(checkout), "session-recovery-tree:" + canonical(checkout)):
                yield


def inspect_recovery(project, chat):
    with _locked(project, chat):
        return _inspect(project, chat)[0]


def recover_session(project, chat, *, review_token, decision):
    """Apply only a reviewed action; never delete a branch or unknown directory."""
    with _locked(project, chat):
        review, before, after = _inspect(project, chat)
        if not review_token or review["review_token"] != review_token:
            raise RuntimeError("Recovery baseline changed; inspect and review again")
        if decision not in review["actions"]:
            raise RuntimeError("Recovery action is unsafe: " + "; ".join(review["issues"]))
        facts = review["facts"]
        operation = review["operation"]
        manifest, checkout, branch = _paths(project, chat)
        if decision == "cancel":
            target = before
        else:
            target = after
            if operation in {"create", "restore"} and facts["registered_worktree"] is None:
                if facts["branch_head"] is None:
                    git(project, "worktree", "add", "-b", branch, str(checkout), after.base_commit)
                else:
                    git(project, "worktree", "add", str(checkout), branch)
            elif operation == "archive" and facts["registered_worktree"] is not None:
                from research_agent.sandbox import close_workspace_sessions
                close_workspace_sessions(str(checkout))
                git(project, "worktree", "remove", str(checkout))
        if target is not None:
            _validate(target)
            _publish_binding(manifest, target)
            _publish_binding(_binding_path(chat), target)
        else:
            # No branch, directory or manifest exists; only remove our barrier.
            _binding_path(chat).unlink()
        from research_agent.review_baseline import baseline
        baseline(project, "session-recovery:" + chat, {"review": review, "decision": decision,
                                                      "result": asdict(target) if target else None})
        return {"status": "recovered", "decision": decision, "binding": asdict(target) if target else None}
