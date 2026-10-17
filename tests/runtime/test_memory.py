"""真实Git与SQLite验证记忆来源、作用域、失效和人工生命周期。"""

from datetime import datetime, timedelta, timezone
from contextlib import closing
import json

import pytest

from repopilot.runtime.memory import MemoryStore, MAX_CONTEXT_BYTES
from repopilot.runtime.memory_facts import MemoryFault, capture, repository_id
from repopilot.runtime.workspace import run_git
from tests.runtime.test_review_loop import make_repo


def add_file(repo, name, content):
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    run_git(repo, 'add', '--all')
    run_git(repo, 'commit', '-qm', 'fixture')


def test_source_proof_persists_without_cross_repository_reads(tmp_path):
    repo = make_repo(tmp_path)
    other_root = tmp_path / 'other'
    other_root.mkdir()
    other = make_repo(other_root)
    store = MemoryStore(tmp_path / 'runtime')
    record = store.remember(repo, 'app.py', 1, 1)
    assert record['content'] == 'value = 1'
    assert record['source_commit'] == run_git(repo, 'rev-parse', 'HEAD').stdout.decode().strip()
    assert MemoryStore(tmp_path / 'runtime').search(repo, 'app')['entries'][0]['id'] == record['id']
    assert not store.search(other, 'app')['entries']
    with pytest.raises(MemoryFault, match='memory_not_found'):
        store.get(other, record['id'])


def test_worktrees_share_identity_and_unrelated_commit_keeps_fact(tmp_path):
    from repopilot.runtime.workspace import Workspace
    repo = make_repo(tmp_path)
    store = MemoryStore(tmp_path / 'runtime')
    record = store.remember(repo, 'app.py', 1, 1)
    worktree = Workspace(repo, tmp_path / 'worktrees').prepare()
    assert repository_id(repo) == repository_id(worktree.repository())
    assert store.search(worktree.repository(), 'app')['entries']
    add_file(repo, 'unrelated.txt', 'unrelated\n')
    assert store.remember(repo, 'app.py', 1, 1)['id'] == record['id']
    assert store.search(repo, 'app')['entries']


def test_changed_source_or_dependency_is_omitted(tmp_path):
    repo = make_repo(tmp_path)
    add_file(repo, 'package.json', '{"version":"1"}\n')
    store = MemoryStore(tmp_path / 'runtime')
    record = store.remember(repo, 'app.py', 1, 1, dependencies=['package.json'])
    (repo / 'package.json').write_text('{"version":"2"}\n')
    result = store.search(repo, 'app')
    assert not result['entries'] and result['omitted'][0]['reason'] == 'source_changed'
    with pytest.raises(MemoryFault, match='memory_source_changed'):
        store.decide(repo, record['id'], 'confirm', '尝试重验')
    (repo / 'app.py').unlink()
    assert store.search(repo, 'app')['omitted'][0]['reason'] == 'source_unavailable'


def test_candidates_require_confirmation_and_do_not_resurrect(tmp_path):
    repo = make_repo(tmp_path)
    store = MemoryStore(tmp_path / 'runtime')
    record = store.remember(repo, 'app.py', 1, 1, candidate=True, source_run='previous')
    assert not store.search(repo, 'app')['entries']
    store.decide(repo, record['id'], 'confirm', '核对源码')
    snapshot = store.search(repo, 'app')
    assert snapshot['entries']
    store.decide(repo, record['id'], 'revoke', '暂停使用')
    assert not store.view(repo, snapshot)['entries']
    store.decide(repo, record['id'], 'forget', '清理事实')
    assert store.get(repo, record['id'])['content'] == ''
    add_file(repo, 'unrelated.txt', 'next commit\n')
    assert store.remember(repo, 'app.py', 1, 1, candidate=True)['status'] == 'deleted'
    with pytest.raises(MemoryFault, match='memory_cannot_resurrect'):
        store.decide(repo, record['id'], 'confirm', '不允许复活')


def test_expired_fact_requires_explicit_revalidation(tmp_path, monkeypatch):
    from repopilot.runtime import memory
    repo = make_repo(tmp_path)
    store = MemoryStore(tmp_path / 'runtime')
    record = store.remember(repo, 'app.py', 1, 1)
    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(timezone.utc) + timedelta(days=31)
    monkeypatch.setattr(memory, 'datetime', Future)
    assert store.search(repo, 'app')['omitted'][0]['reason'] == 'expired'
    store.decide(repo, record['id'], 'confirm', '重新核对来源')
    assert store.search(repo, 'app')['entries']


@pytest.mark.parametrize('path', ['.env.example', 'evals/answer.py', 'oracle/test.py', '../app.py'])
def test_sensitive_or_evaluation_paths_are_not_memory(tmp_path, path):
    repo = make_repo(tmp_path)
    with pytest.raises((MemoryFault, ValueError)):
        capture(repo, path, 1, 1)


def test_dirty_source_and_possible_secrets_are_not_stored(tmp_path):
    repo = make_repo(tmp_path)
    store = MemoryStore(tmp_path / 'runtime')
    (repo / 'app.py').write_text('value = 2\n')
    with pytest.raises(MemoryFault, match='uncommitted'):
        store.remember(repo, 'app.py', 1, 1)
    add_file(repo, 'app.py', "token = 'synthetic_sensitive_value'\n")
    with pytest.raises(MemoryFault, match='possible_secret'):
        store.remember(repo, 'app.py', 1, 1)


def test_retrieval_is_bounded_and_snapshot_does_not_gain_new_facts(tmp_path):
    repo = make_repo(tmp_path)
    add_file(repo, 'module.py', '\n'.join(f'value_{i} = {i}' for i in range(8)) + '\n')
    store = MemoryStore(tmp_path / 'runtime')
    for line in range(1, 8):
        store.remember(repo, 'module.py', line, line)
    snapshot = store.search(repo, 'module value')
    assert len(snapshot['entries']) == 5
    assert len(json.dumps(snapshot['entries'], ensure_ascii=False).encode()) <= MAX_CONTEXT_BYTES
    old_ids = [entry['id'] for entry in snapshot['entries']]
    store.remember(repo, 'module.py', 8, 8)
    assert [entry['id'] for entry in store.view(repo, snapshot)['entries']] == old_ids
    assert not store.search(repo, 'unrelated')['entries']


def test_candidate_backlog_does_not_hide_active_memory(tmp_path):
    repo = make_repo(tmp_path)
    store = MemoryStore(tmp_path / 'runtime')
    record = store.remember(repo, 'app.py', 1, 1)
    with closing(store.connect()) as db, db:
        for number in range(201):
            candidate = record | {'id': f'candidate-{number}', 'status': 'candidate'}
            db.execute('INSERT INTO facts VALUES (?, ?, ?, ?, ?)',
                       (candidate['id'], candidate['repository_id'], 'candidate', json.dumps(candidate), '2099'))
    assert store.search(repo, 'app')['entries'][0]['id'] == record['id']
