import os
import pytest
from research_agent.proposal import ProposalManager
from research_agent.tools.git_tool import git_init, _run_git

@pytest.fixture
def repo(tmp_path):
    d = str(tmp_path)
    git_init(d)
    with open(os.path.join(d, 'tracked.txt'), 'w', encoding='utf-8') as f:
        f.write('original\n')
    _run_git(['add', '-A'], d)
    _run_git(['commit', '-m', 'init'], d)
    ProposalManager(d).begin_execution()
    return d

def test_non_repo_returns_empty(tmp_path):
    d = str(tmp_path / 'notgit')
    os.makedirs(d)
    pm = ProposalManager(d)
    assert pm.is_repo() is False
    assert pm.collect() == []

def test_reviewed_file_changed_by_user_is_not_undone(repo):
    path = os.path.join(repo, 'tracked.txt')
    with open(path, 'w') as stream:
        stream.write('agent edit')
    manager = ProposalManager(repo)
    manager.collect()
    with open(path, 'w') as stream:
        stream.write('user edit after review')
    assert not _reviewed_undo(manager, 'undo', ['tracked.txt'])['success']
    with open(path) as stream:
        assert stream.read() == 'user edit after review'

def test_review_baseline_survives_manager_recreation(repo):
    path = os.path.join(repo, 'tracked.txt')
    with open(path, 'w') as stream:
        stream.write('agent output')
    ProposalManager(repo).collect()
    with open(path, 'w') as stream:
        stream.write('human correction')
    manager = ProposalManager(repo)
    manager.collect()
    result = _reviewed_undo(manager, 'undo_all')
    assert not result['success']
    assert 'tracked.txt' in result['conflicts']
    with open(path) as stream:
        assert stream.read() == 'human correction'

def test_collect_modified_file(repo):
    with open(os.path.join(repo, 'tracked.txt'), 'w', encoding='utf-8') as f:
        f.write('original\nadded line\n')
    changes = ProposalManager(repo).collect()
    assert len(changes) == 1
    c = changes[0]
    assert c.path == 'tracked.txt'
    assert c.status == 'modified'
    assert c.additions >= 1
    assert 'added line' in c.diff

def test_collect_added_file(repo):
    with open(os.path.join(repo, 'new.txt'), 'w', encoding='utf-8') as f:
        f.write('hello\nworld\n')
    changes = ProposalManager(repo).collect()
    assert any((c.path == 'new.txt' and c.status == 'added' for c in changes))

def test_undo_modified_restores_original(repo):
    with open(os.path.join(repo, 'tracked.txt'), 'w', encoding='utf-8') as f:
        f.write('changed\n')
    pm = ProposalManager(repo)
    _reviewed_undo(pm, 'undo', ['tracked.txt'])
    with open(os.path.join(repo, 'tracked.txt'), encoding='utf-8') as f:
        assert f.read() == 'original\n'

def test_undo_added_deletes_file(repo):
    path = os.path.join(repo, 'new.txt')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('temp\n')
    _reviewed_undo(ProposalManager(repo), 'undo', ['new.txt'])
    assert not os.path.exists(path)

def test_keep_commits_change(repo):
    with open(os.path.join(repo, 'tracked.txt'), 'w', encoding='utf-8') as f:
        f.write('kept\n')
    pm = ProposalManager(repo)
    res = pm.keep(['tracked.txt'], 'accept test')
    assert res['success']
    assert pm.collect(['tracked.txt']) == []
    with open(os.path.join(repo, 'tracked.txt'), encoding='utf-8') as f:
        assert f.read() == 'kept\n'

def test_keep_then_undo_does_not_revert_committed(repo):
    """After keep, the change is committed → undo should not lose it."""
    with open(os.path.join(repo, 'tracked.txt'), 'w', encoding='utf-8') as f:
        f.write('kept\n')
    pm = ProposalManager(repo)
    pm.keep(['tracked.txt'])
    _reviewed_undo(pm, 'undo', ['tracked.txt'])
    with open(os.path.join(repo, 'tracked.txt'), encoding='utf-8') as f:
        assert f.read() == 'kept\n'

def test_keep_all_and_undo_all(repo):
    for name, body in (('a.txt', 'A\n'), ('b.txt', 'B\n')):
        with open(os.path.join(repo, name), 'w', encoding='utf-8') as f:
            f.write(body)
    pm = ProposalManager(repo)
    assert len(pm.collect()) == 2
    pm.keep_all('accept all')
    assert pm.collect() == []
    with open(os.path.join(repo, 'a.txt'), 'w', encoding='utf-8') as f:
        f.write('A2\n')
    with open(os.path.join(repo, 'c.txt'), 'w', encoding='utf-8') as f:
        f.write('C\n')
    _reviewed_undo(pm, 'undo_all')
    assert pm.collect() == []
    with open(os.path.join(repo, 'a.txt'), encoding='utf-8') as f:
        assert f.read() == 'A\n'
    assert not os.path.exists(os.path.join(repo, 'c.txt'))


def _reviewed_undo(manager, action, *args):
    if not manager._reviewed:
        manager.collect()
    return getattr(manager, action)(*args, review_token=manager.review_token)


def test_undo_without_token_is_rejected(repo):
    manager = ProposalManager(repo)
    path = os.path.join(repo, "tracked.txt")
    with open(path, "w") as stream:
        stream.write("agent")
    manager.collect()
    assert not manager.undo(["tracked.txt"])["success"]
    assert not manager.undo(["tracked.txt"], review_token="forged")["success"]


def test_missing_start_baseline_is_rejected(repo):
    from research_agent.review_baseline import baseline
    baseline(repo, "execution_dirty", {})
    path = os.path.join(repo, "tracked.txt")
    with open(path, "w") as stream:
        stream.write("unknown origin")
    manager = ProposalManager(repo)
    manager.collect()
    result = manager.undo(["tracked.txt"], review_token=manager.review_token)
    assert not result["success"]


def test_binary_file_is_not_deleted_even_after_review(repo):
    path = os.path.join(repo, "binary.dat")
    with open(path, "wb") as stream:
        stream.write(b"binary\0data")
    manager = ProposalManager(repo)
    manager.collect()
    result = manager.undo(["binary.dat"], review_token=manager.review_token)
    assert not result["success"]
    assert os.path.exists(path)


def test_mixed_human_agent_file_is_rejected(repo):
    path = os.path.join(repo, "tracked.txt")
    with open(path, "w") as stream:
        stream.write("human uncommitted")
    manager = ProposalManager(repo)
    manager.begin_execution()
    with open(path, "a") as stream:
        stream.write(" plus agent")
    manager.collect()
    result = manager.undo(["tracked.txt"], review_token=manager.review_token)
    assert not result["success"]
    assert "preexisting" in result["reason"]
    with open(path) as stream:
        assert stream.read() == "human uncommitted plus agent"
