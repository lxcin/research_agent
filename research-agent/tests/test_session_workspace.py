from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
import json
import pytest
from click.testing import CliRunner

from research_agent.session_workspace import prepare_session, inspect_session, merge_session, git


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "code.txt").write_text("initial", encoding="utf-8")
    git(root, "add", "code.txt")
    git(root, "commit", "-m", "initial")
    from research_agent import project_manager as pm
    pm.init_project(str(root), "shared project")
    return root


def commit(binding, text):
    (Path(binding.workspace) / "code.txt").write_text(text, encoding="utf-8")
    git(binding.workspace, "add", "code.txt")
    git(binding.workspace, "commit", "-m", "session change")
    return git(binding.workspace, "rev-parse", "HEAD")


def test_sessions_have_independent_branches_indexes_and_files(project):
    a, b = prepare_session(str(project), "a"), prepare_session(str(project), "b")
    assert a.branch != b.branch and a.workspace != b.workspace
    assert a.branch.startswith("codex/session-")
    (Path(a.workspace) / "code.txt").write_text("a", encoding="utf-8")
    git(a.workspace, "add", "code.txt")
    assert git(a.workspace, "diff", "--cached", "--name-only") == "code.txt"
    assert git(b.workspace, "diff", "--cached", "--name-only") == ""
    assert (Path(b.workspace) / "code.txt").read_text() == "initial"
    assert (project / "code.txt").read_text() == "initial"


def test_reopen_preserves_uncommitted_session_work(project):
    a = prepare_session(str(project), "a")
    (Path(a.workspace) / "code.txt").write_text("unfinished", encoding="utf-8")
    assert prepare_session(str(project), "a") == a
    assert (Path(a.workspace) / "code.txt").read_text() == "unfinished"


def test_dirty_source_rejects_new_session_without_discarding_edits(project):
    (project / "code.txt").write_text("human", encoding="utf-8")
    with pytest.raises(RuntimeError, match="uncommitted"):
        prepare_session(str(project), "a")
    assert (project / "code.txt").read_text() == "human"


def test_branch_switch_is_not_silently_reset(project):
    a = prepare_session(str(project), "a")
    git(a.workspace, "checkout", "--detach")
    with pytest.raises(RuntimeError):
        prepare_session(str(project), "a")


def test_two_sessions_run_concurrently_and_keep_original_chat_history(project, monkeypatch):
    from research_agent import agent, project_manager as pm
    from research_agent.models import AgentState
    from research_agent.execution import exclusive_request
    a, b = prepare_session(str(project), "a"), prepare_session(str(project), "b")
    barrier = Barrier(2)
    def impl(user_input, llm, state, on_event, workspace, chat, **kwargs):
        state.workspace_dir = workspace
        assert pm.is_project_dir(workspace)
        assert pm.get_project_id(workspace) == pm.get_project_id(str(project))
        barrier.wait(timeout=5)
        (Path(workspace) / "code.txt").write_text(chat)
        pm.save_turn(workspace, chat, 1, user_input, "done")
        return state
    monkeypatch.setattr(agent, "_run_agent_impl", impl)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(agent.run_agent, "one", None, AgentState(), workspace_dir=str(project), chat_id="a", isolate_session=True)
        second = pool.submit(agent.run_agent, "two", None, AgentState(), workspace_dir=str(project), chat_id="b", isolate_session=True)
        first.result(10); second.result(10)
    assert (Path(a.workspace) / "code.txt").read_text() == "a"
    assert (Path(b.workspace) / "code.txt").read_text() == "b"
    assert len(pm.get_recent_turns(str(project), "a")) == 1
    with exclusive_request(a.workspace, "a"):
        with pytest.raises(RuntimeError):
            with exclusive_request(a.workspace, "a"):
                pass


def test_proposals_commit_only_session_branch_and_integration_is_explicit(project):
    from research_agent.proposal import ProposalManager
    a = prepare_session(str(project), "a")
    initial = git(project, "rev-parse", "HEAD")
    proposals = ProposalManager(a.workspace)
    proposals.begin_execution()
    (Path(a.workspace) / "code.txt").write_text("reviewed", encoding="utf-8")
    assert proposals.collect()
    assert proposals.keep(["code.txt"])["success"]
    session_head = git(a.workspace, "rev-parse", "HEAD")
    assert git(project, "rev-parse", "HEAD") == initial
    merge_session(str(project), "a", expected_project_head=initial, expected_session_head=session_head)
    assert (project / "code.txt").read_text() == "reviewed"


def test_diverged_sessions_cannot_overwrite_project_on_integration(project):
    a, b = prepare_session(str(project), "a"), prepare_session(str(project), "b")
    original = git(project, "rev-parse", "HEAD")
    head_a, head_b = commit(a, "a"), commit(b, "b")
    merge_session(str(project), "a", expected_project_head=original, expected_session_head=head_a)
    with pytest.raises(RuntimeError):
        merge_session(str(project), "b", expected_project_head=head_a, expected_session_head=head_b)
    assert (project / "code.txt").read_text() == "a"
    assert (Path(b.workspace) / "code.txt").read_text() == "b"
    assert not Path(git(project, "rev-parse", "--git-path", "MERGE_HEAD")).exists()


def test_stale_integration_baseline_and_dirty_worktree_are_rejected(project):
    a = prepare_session(str(project), "a")
    initial = git(project, "rev-parse", "HEAD")
    head = commit(a, "new")
    with pytest.raises(RuntimeError, match="baseline"):
        merge_session(str(project), "a", expected_project_head=initial, expected_session_head=initial)
    (Path(a.workspace) / "code.txt").write_text("later")
    with pytest.raises(RuntimeError, match="Commit reviewed"):
        merge_session(str(project), "a", expected_project_head=initial, expected_session_head=head)


def test_docker_mount_uses_session_checkout(project):
    from research_agent.sandbox import build_session_argv, SandboxConfig
    a, b = prepare_session(str(project), "a"), prepare_session(str(project), "b")
    mounts = []
    for session in (a, b):
        argv = build_session_argv(session.workspace, SandboxConfig(), "test-container")
        mounts.append(argv[argv.index("-v") + 1])
    assert mounts[0] != mounts[1]
    assert all(mount.endswith(":/work") for mount in mounts)


def test_cli_info_and_missing_session_rejection(project):
    from research_agent.cli import main
    a = prepare_session(str(project), "a")
    result = CliRunner().invoke(main, ["session-workspace", "info", "--project", str(project), "--chat", "a"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["branch"] == a.branch
    assert inspect_session(str(project), "unknown") is None
    with pytest.raises(ValueError):
        prepare_session(str(project), "")


def test_full_agent_routes_files_checkpoints_proposals_and_history_to_session(project, monkeypatch):
    from research_agent.agent import run_agent
    from research_agent.models import AgentState
    from research_agent.kernel import AgentKernel, KernelResult, ToolCall
    from research_agent.tools import ToolRegistry
    from research_agent import project_manager as pm
    monkeypatch.setattr(ToolRegistry, "_instance", None)
    class Writer(AgentKernel):
        id = "session_test_writer"
        def run(self, host, request):
            observed = host.execute(ToolCall("read", "file_read", {"path": "code.txt"}))
            assert observed.success, observed.data
            assert host.execute(ToolCall("write", "file_write", {"expected_version": observed.data["version"], "path": "code.txt", "content": "isolated"})).success
            return KernelResult("done", 1)
    result = run_agent("write", None, AgentState(), workspace_dir=str(project), chat_id="full", isolate_session=True, kernel=Writer())
    assert result.session_branch.startswith("codex/session-")
    assert result.pending_proposals
    assert (project / "code.txt").read_text() == "initial"
    assert (Path(result.workspace_dir) / "code.txt").read_text() == "isolated"
    assert result._last_checkpoint
    assert pm.get_recent_turns(str(project), "full")[0]["assistant"] == "done"


def test_resume_physical_worktree_preserves_binding_and_rejects_other_chat(project, monkeypatch):
    from research_agent import agent
    from research_agent.models import AgentState
    a = prepare_session(str(project), "a")
    monkeypatch.setattr(agent, "_run_agent_impl", lambda user_input, llm, state, *args, **kwargs: state)
    result = agent.run_agent("resume", None, AgentState(), workspace_dir=a.workspace, chat_id="a")
    assert result.session_branch == a.branch
    with pytest.raises(ValueError, match="belong"):
        agent.run_agent("wrong", None, AgentState(), workspace_dir=a.workspace, chat_id="b")


def test_omitted_flag_routes_fresh_state_to_persisted_session(project, monkeypatch):
    from research_agent import agent
    from research_agent.models import AgentState
    binding = prepare_session(str(project), "bound")
    events = []
    def write(user_input, llm, state, emit, workspace, chat, **kwargs):
        state.workspace_dir = workspace
        (Path(workspace) / "code.txt").write_text("session only")
        return state
    monkeypatch.setattr(agent, "_run_agent_impl", write)
    result = agent.run_agent("next", None, AgentState(), workspace_dir=str(project), chat_id="bound",
                             on_event=lambda kind, data: events.append((kind, data)))
    assert result.workspace_dir == binding.workspace
    assert result.session_branch == binding.branch
    assert (project / "code.txt").read_text() == "initial"
    assert events[0][0] == "session_workspace"


def test_explicit_shared_mode_and_wrong_project_are_rejected_before_execution(project, monkeypatch, tmp_path):
    from research_agent import agent
    from research_agent.models import AgentState
    binding = prepare_session(str(project), "bound")
    monkeypatch.setattr(agent, "_run_agent_impl", lambda *a, **k: pytest.fail("must not execute"))
    for workspace in (str(project), binding.workspace):
        with pytest.raises(ValueError, match="shared mode"):
            agent.run_agent("bad", None, AgentState(), workspace_dir=workspace, chat_id="bound", isolate_session=False)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="another workspace"):
        agent.run_agent("bad", None, AgentState(), workspace_dir=str(other), chat_id="bound")
    with pytest.raises(ValueError, match="another project"):
        prepare_session(str(other), "bound")


def test_legacy_manifest_is_adopted_and_missing_manifest_never_falls_back(project):
    from research_agent.session_workspace import _binding_path, resolve_session
    binding = prepare_session(str(project), "legacy")
    index = _binding_path("legacy")
    index.unlink()  # Simulate a worktree created before persistent chat routing.
    assert resolve_session(str(project), "legacy") == binding
    assert index.exists()
    (Path(binding.workspace).parent / "session.json").unlink()
    with pytest.raises(RuntimeError, match="missing or changed"):
        resolve_session(str(project), "legacy")


def test_creation_failure_leaves_pending_binding_instead_of_shared_fallback(project, monkeypatch):
    from research_agent import session_workspace as sessions
    original = sessions.git
    def fail(root, *args):
        if args[:2] == ("worktree", "add"):
            raise RuntimeError("creation interrupted")
        return original(root, *args)
    monkeypatch.setattr(sessions, "git", fail)
    with pytest.raises(RuntimeError, match="creation interrupted"):
        prepare_session(str(project), "pending")
    with pytest.raises(RuntimeError, match="incomplete"):
        sessions.resolve_session(str(project), "pending")


def test_admission_rechecks_binding_after_shared_routing(project, monkeypatch):
    from research_agent import agent, session_workspace as sessions
    from research_agent.models import AgentState
    original = sessions.resolve_session
    def race(workspace, chat, mode=None):
        assert original(workspace, chat, mode) is None
        prepare_session(workspace, chat)
        return None  # Simulate a shared request resolved just before isolation was created.
    monkeypatch.setattr(sessions, "resolve_session", race)
    monkeypatch.setattr(agent, "_run_agent_impl", lambda *a, **k: pytest.fail("must not execute"))
    with pytest.raises(RuntimeError, match="changed during admission"):
        agent.run_agent("race", None, AgentState(), workspace_dir=str(project), chat_id="race")


def test_api_distinguishes_omitted_mode_from_explicit_shared():
    from research_agent.server import ChatRequest
    assert ChatRequest(message="next").isolate_session is None
    assert ChatRequest(message="next", isolate_session=False).isolate_session is False
