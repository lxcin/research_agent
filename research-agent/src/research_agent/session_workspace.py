"""Opt-in durable session branches backed by separate Git worktrees."""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
import re
from pathlib import Path
import tempfile


def canonical(path):
    return os.path.normcase(os.path.realpath(path))


def git(root, *args):
    from research_agent.tools.git_tool import _run_git
    result = _run_git(list(args), str(root), timeout=60)
    if not result["success"]:
        raise RuntimeError(result.get("stderr") or result.get("stdout") or "Git operation failed")
    return result["stdout"]


@dataclass(frozen=True)
class SessionWorkspace:
    project: str
    chat_id: str
    workspace: str
    branch: str
    base_commit: str
    project_branch: str
    archived: bool = False
    archived_head: str = ""


def _paths(project, chat_id):
    from research_agent.config import get_data_dir
    identity = hashlib.sha256((canonical(project) + "\0" + chat_id).encode()).hexdigest()
    folder = get_data_dir() / "session_worktrees" / identity
    return folder / "session.json", folder / "checkout", "codex/session-" + identity[:24]


def project_workspace(workspace):
    """Map physical worktree to logical project for conversation metadata only."""
    path = Path(workspace).resolve()
    manifest = path.parent / "session.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if canonical(data.get("workspace", "")) == canonical(path):
            expected, checkout, branch = _paths(data["project"], data["chat_id"])
            if expected.resolve() == manifest.resolve() and canonical(checkout) == canonical(path) and data["branch"] == branch:
                return data["project"]
    return workspace


def _validate(binding):
    if binding.archived:
        if Path(binding.workspace).exists():
            raise RuntimeError("Archived session directory unexpectedly exists")
        if git(binding.project, "rev-parse", "refs/heads/" + binding.branch) != binding.archived_head:
            raise RuntimeError("Archived branch changed; explicit reconciliation required")
        return binding
    if not Path(binding.workspace).is_dir():
        raise RuntimeError("Session worktree is missing; restore it explicitly, never recreate over user data")
    if git(binding.workspace, "symbolic-ref", "--short", "HEAD") != binding.branch:
        raise RuntimeError("Session worktree branch changed; explicit reconciliation required")
    if canonical(git(binding.workspace, "rev-parse", "--path-format=absolute", "--git-common-dir")) != canonical(
            git(binding.project, "rev-parse", "--path-format=absolute", "--git-common-dir")):
        raise RuntimeError("Session worktree repository mismatch")
    return binding


def inspect_session(project, chat_id):
    manifest, checkout, branch = _paths(project, chat_id)
    if not manifest.exists():
        return None
    binding = SessionWorkspace(**json.loads(manifest.read_text(encoding="utf-8")))
    if canonical(binding.project) != canonical(project) or binding.chat_id != chat_id or canonical(binding.workspace) != canonical(checkout) or binding.branch != branch:
        raise RuntimeError("Session workspace identity mismatch")
    return _validate(binding)


def prepare_session(project, chat_id):
    from research_agent.docker_ownership import reconcile_before_request
    reconcile_before_request()
    from research_agent.background_tasks import recover_workspace
    recover_workspace(project)
    if not isinstance(chat_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", chat_id):
        raise ValueError("Isolated sessions require a stable chat_id")
    from research_agent.execution import exclusive_request
    index = _binding_path(chat_id)
    # Serialize creation across projects for this globally admitted chat identity.
    with exclusive_request(str(index), chat_id):
        previous = bound_session(chat_id)
        logical = project_workspace(project)
        if previous is not None and canonical(previous.project) != canonical(logical):
            raise ValueError("Session is already bound to another project")
        binding = _prepare_session(project, chat_id)
        _publish_binding(index, binding)
        return binding


def _binding_path(chat_id):
    from research_agent.config import get_data_dir
    return get_data_dir() / "session_bindings" / (hashlib.sha256(chat_id.encode()).hexdigest() + ".json")


def _publish_binding(path, binding):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(asdict(binding) if isinstance(binding, SessionWorkspace) else binding, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _begin_operation(operation, before, after):
    import uuid
    common = canonical(git(after.project, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    identity = Path(common).stat()
    _publish_binding(_binding_path(after.chat_id), {
        "status": "preparing", "version": 1, "operation": operation,
        "operation_id": uuid.uuid4().hex, "chat_id": after.chat_id, "project": after.project,
        "before": asdict(before) if before is not None else None, "after": asdict(after),
        "repository": common, "repository_id": [identity.st_dev, identity.st_ino],
    })


def bound_session(chat_id):
    if not chat_id:
        return None
    path = _binding_path(chat_id)
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("status") == "preparing":
        raise RuntimeError("Session binding incomplete; explicit reconciliation required")
    data = SessionWorkspace(**raw)
    if data.chat_id != chat_id:
        raise RuntimeError("Session binding identity mismatch")
    actual = inspect_session(data.project, chat_id)
    if actual != data:
        raise RuntimeError("Session binding missing or changed; explicit reconciliation required")
    return actual


def resolve_session(workspace, chat_id, isolate_session=None):
    """None means unspecified; False explicitly requests shared mode."""
    binding = bound_session(chat_id)
    logical = project_workspace(workspace)
    if binding is None and chat_id:
        # Adopt manifests written before the durable chat index existed.
        legacy = inspect_session(logical, chat_id)
        if legacy is not None:
            binding = prepare_session(logical, chat_id)
    if binding is not None:
        if canonical(workspace) not in (canonical(binding.project), canonical(binding.workspace)):
            raise ValueError("Session is already bound to another workspace/project")
        if isolate_session is False:
            raise ValueError("Isolated session cannot switch to shared mode")
        if binding.archived:
            raise RuntimeError("Session is archived; use session-workspace restore before running")
        return binding
    if canonical(logical) != canonical(workspace):
        raise ValueError("Session worktree does not belong to this chat")
    return prepare_session(workspace, chat_id) if isolate_session else None


def _prepare_session(project, chat_id):
    if not isinstance(chat_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", chat_id):
        raise ValueError("Isolated sessions require a stable chat_id")
    project = str(Path(project_workspace(project)).resolve())
    manifest, checkout, branch = _paths(project, chat_id)
    if checkout.resolve().is_relative_to(Path(project).resolve()):
        raise ValueError("Session worktree storage must be outside the source repository")
    existing = inspect_session(project, chat_id)
    if existing is not None:
        return existing
    from research_agent.execution import exclusive_request
    with exclusive_request(project, "session-create:" + canonical(project)):
        existing = inspect_session(project, chat_id)
        if existing is not None:
            return existing
        top = git(project, "rev-parse", "--show-toplevel")
        if canonical(top) != canonical(project):
            raise ValueError("Session isolation requires the repository root")
        base = git(project, "rev-parse", "--verify", "HEAD^{commit}")
        project_branch = git(project, "symbolic-ref", "--short", "HEAD")
        # Never silently omit human edits from the new session's initial files.
        dirty = git(project, "status", "--porcelain", "--untracked-files=normal", "--", ".", ":(exclude).research-agent")
        if dirty:
            raise RuntimeError("Project has uncommitted changes; commit or resolve them before creating a new session worktree")
        manifest, checkout, branch = _paths(project, chat_id)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        if checkout.exists():
            raise RuntimeError("Unregistered session directory exists; refusing to overwrite")
        binding = SessionWorkspace(project, chat_id, str(checkout), branch, base, project_branch)
        _begin_operation("create", None, binding)
        # Git refuses a pre-existing branch; no force/reset/automatic deletion.
        git(project, "worktree", "add", "-b", branch, str(checkout), base)
        _publish_binding(manifest, binding)
        return binding


def merge_session(project, chat_id, *, expected_project_head, expected_session_head):
    """Explicit fast-forward integration only; divergence leaves both worktrees intact."""
    from research_agent.docker_ownership import reconcile_before_request
    reconcile_before_request()
    from research_agent.execution import exclusive_request
    binding = inspect_session(project, chat_id)
    if binding is None:
        raise ValueError("Unknown session workspace")
    if binding.archived:
        raise ValueError("Session is archived")
    from research_agent.background_tasks import recover_workspace
    recover_workspace(binding.project)
    recover_workspace(binding.workspace)
    with exclusive_request(binding.project, "session-merge-project:" + canonical(binding.project)):
        with exclusive_request(binding.workspace, chat_id):
            _validate(binding)
            if git(binding.project, "symbolic-ref", "--short", "HEAD") != binding.project_branch:
                raise RuntimeError("Project branch changed; explicit reconciliation required")
            if git(binding.project, "rev-parse", "HEAD") != expected_project_head or git(binding.workspace, "rev-parse", "HEAD") != expected_session_head:
                raise RuntimeError("Review baseline changed; inspect both branches again")
            if git(binding.project, "status", "--porcelain", "--", ".", ":(exclude).research-agent") or git(binding.workspace, "status", "--porcelain", "--", ".", ":(exclude).research-agent"):
                raise RuntimeError("Commit reviewed session changes and resolve project changes before integration")
            git(binding.project, "merge", "--ff-only", expected_session_head)
            return {"project": binding.project, "branch": binding.branch, "head": git(binding.project, "rev-parse", "HEAD")}


def session_storage(project, chat_id):
    """Logical bytes, not physical disk allocation; do not follow links/junctions."""
    binding = inspect_session(project, chat_id)
    if binding is None:
        raise ValueError("Unknown session")
    files, size, links = 0, 0, 0
    for directory, dirs, names in os.walk(binding.workspace, followlinks=False):
        safe = []
        for name in dirs:
            path = Path(directory, name)
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                links += 1
            else:
                safe.append(name)
        dirs[:] = safe
        for name in names:
            path = Path(directory, name)
            if path.is_symlink():
                links += 1
            else:
                size += path.stat().st_size
                files += 1
    return {"workspace": binding.workspace, "archived": binding.archived, "logical_bytes": size,
            "files": files, "skipped_links": links, "includes_git_objects": False}


def archive_session(project, chat_id, *, expected_project_head, expected_session_head):
    from research_agent.docker_ownership import reconcile_before_request
    reconcile_before_request()
    from dataclasses import replace
    from research_agent.execution import exclusive_request
    binding = inspect_session(project, chat_id)
    if binding is None or binding.archived:
        raise ValueError("Expected active session")
    from research_agent.background_tasks import recover_workspace
    recover_workspace(binding.project)
    recover_workspace(binding.workspace)
    with exclusive_request(binding.project, "archive:" + canonical(binding.project)):
        with exclusive_request(binding.workspace, chat_id):
            if bound_session(chat_id) != binding:
                raise RuntimeError("Session binding changed")
            if git(binding.project, "rev-parse", "HEAD") != expected_project_head or git(binding.workspace, "rev-parse", "HEAD") != expected_session_head:
                raise RuntimeError("Review baseline changed")
            git(binding.project, "merge-base", "--is-ancestor", expected_session_head, expected_project_head)
            # Include ignored data: successful code integration does not back up .env or datasets.
            if git(binding.workspace, "status", "--porcelain", "--untracked-files=all", "--ignored"):
                raise RuntimeError("Session contains modified, untracked or ignored data; preserve it before archiving")
            from research_agent.sandbox import close_workspace_sessions
            close_workspace_sessions(binding.workspace)
            archived = replace(binding, archived=True, archived_head=expected_session_head)
            _begin_operation("archive", binding, archived)
            # No --force, no branch deletion, no recursive filesystem deletion.
            git(binding.project, "worktree", "remove", binding.workspace)
            _publish_binding(_paths(project, chat_id)[0], archived)
            _publish_binding(_binding_path(chat_id), archived)
            return archived


def restore_session(project, chat_id):
    from research_agent.docker_ownership import reconcile_before_request
    reconcile_before_request()
    from dataclasses import replace
    from research_agent.execution import exclusive_request
    binding = inspect_session(project, chat_id)
    if binding is None or not binding.archived:
        raise ValueError("Expected archived session")
    from research_agent.background_tasks import recover_workspace
    recover_workspace(binding.project)
    recover_workspace(binding.workspace)
    with exclusive_request(binding.project, "restore:" + canonical(binding.project)):
        with exclusive_request(binding.workspace, chat_id):
            if bound_session(chat_id) != binding:
                raise RuntimeError("Session binding changed")
            _validate(binding)
            restored = replace(binding, archived=False, archived_head="")
            _begin_operation("restore", binding, restored)
            git(binding.project, "worktree", "add", binding.workspace, binding.branch)
            _publish_binding(_paths(project, chat_id)[0], restored)
            _publish_binding(_binding_path(chat_id), restored)
            return restored
