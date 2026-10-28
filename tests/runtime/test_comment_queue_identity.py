"""真实watch调度与SQLite覆盖新旧来源队列；不执行模型／平台worker。"""
from contextlib import closing

import pytest

from repopilot.delivery import watcher
from repopilot.storage.jobs import JobStore, StateError
from tests.runtime.test_watcher import watch_setup, queued_issue


def waiting_queue(tmp_path, *, legacy=False, cross_source=False):
    task, settings, root, config = watch_setup(tmp_path)
    config.max_workers, config.max_per_repository = 2, 1
    queue = watcher.IssueQueue(root)
    urls = ['https://github.com/owner/repo/issues/1',
            'https://gitlab.com/owner/repo/-/issues/1' if cross_source
            else 'https://github.com/owner/repo/issues/2']
    runs = []
    for index, url in enumerate(urls):
        number = 1 if cross_source else index + 1
        queue.enqueue(queued_issue(number) | {'html_url': url}, 'owner/repo', task, {})
        run_id = f'waiting-{index}'
        JobStore(root).create({'run_id': run_id, 'status': 'needs_input',
            'issue': {'repository': 'owner/repo', 'number': number, 'url': url},
            'control': {'model_calls': 7}, 'approval': None})
        with closing(queue.store.connect()) as db, db:
            db.execute("UPDATE issue_queue SET status='needs_input',run_id=? WHERE url=?", (run_id, url))
            if legacy and (not cross_source or index == 0):
                db.execute('UPDATE issue_queue SET issue_key=? WHERE url=?', (f'owner/repo#{number}', url))
        runs.append(run_id)
    return queue, root, config, runs, settings


def dispatch(monkeypatch, root, config, waiting, *, outcome='pending'):
    submissions = []

    class Future:
        def done(self):
            return outcome != 'pending'

        def result(self):
            (root / 'watcher.stop').write_text('fixture finished', encoding='utf-8')
            if outcome == 'exception':
                raise RuntimeError('synthetic worker exit')
            return {'status': 'failed', 'error': 'synthetic worker error'}

    class Pool:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass

        def submit(self, fn, *args):
            with closing(JobStore(root).connect()) as db:
                rows = db.execute('SELECT issue_key,status,run_id FROM issue_queue ORDER BY issue_key').fetchall()
            submissions.append({'run_id': args[-1], 'rows': rows})
            if outcome in {'pending', 'submit_error'}:
                (root / 'watcher.stop').write_text('stop dispatch fixture', encoding='utf-8')
            if outcome == 'submit_error':
                raise RuntimeError('synthetic submission error')
            return Future()

    monkeypatch.setattr(watcher, 'ProcessPoolExecutor', Pool)
    monkeypatch.setattr(watcher, 'poll_once', lambda *args: 0)
    monkeypatch.setattr(watcher.human, 'waiting_comment_runs', lambda *args: waiting)
    monkeypatch.setattr(watcher.time, 'sleep', lambda *args: None)
    watcher.watch(config, root)
    return submissions


@pytest.mark.parametrize('legacy', [False, True])
def test_watch_comment_dispatch_claims_actual_row_and_respects_repository_limit(tmp_path, monkeypatch, legacy):
    queue, root, config, runs, _ = waiting_queue(tmp_path, legacy=legacy)
    submitted = dispatch(monkeypatch, root, config, runs)
    assert [item['run_id'] for item in submitted] == [runs[0]]
    assert sum(row['status'] == 'running' for row in queue.list()) == 1
    assert queue.claim(1) is None


@pytest.mark.parametrize('outcome', ['pending', 'failed', 'exception', 'submit_error'])
def test_cross_source_legacy_row_unchanged_on_dispatch_and_error(tmp_path, monkeypatch, outcome):
    queue, root, config, runs, _ = waiting_queue(tmp_path, legacy=True, cross_source=True)
    before = JobStore(root).get(runs[1])
    submitted = dispatch(monkeypatch, root, config, [runs[1]], outcome=outcome)
    rows = {row[0]: row[1] for row in submitted[0]['rows']}
    assert rows['owner/repo#1'] == 'needs_input'
    assert rows['gitlab:gitlab.com/owner/repo#1'] == 'running'
    final = {row['issue_key']: row for row in queue.list()}
    assert final['owner/repo#1']['status'] == 'needs_input'
    assert final['gitlab:gitlab.com/owner/repo#1']['status'] == (
        'running' if outcome == 'pending' else 'needs_input')
    if outcome != 'pending':
        assert final['gitlab:gitlab.com/owner/repo#1']['error']
    assert JobStore(root).get(runs[1]) == before


def test_comment_worker_success_finishes_only_bound_source(tmp_path, monkeypatch):
    queue, root, _, runs, settings = waiting_queue(tmp_path, legacy=True, cross_source=True)
    assert queue.begin_comment_run(runs[1], 1)
    monkeypatch.setattr(watcher, 'load_settings', lambda: settings)
    monkeypatch.setattr(watcher.workflow, 'resume', lambda *args: {'status': 'awaiting_approval'})
    monkeypatch.setattr(watcher.human, 'sync_issue', lambda *args: {'synced': False})
    assert watcher.run_waiting_job(root, runs[1])['status'] == 'awaiting_approval'
    rows = {row['issue_key']: row['status'] for row in queue.list()}
    assert rows == {'owner/repo#1': 'needs_input',
                    'gitlab:gitlab.com/owner/repo#1': 'awaiting_approval'}


@pytest.mark.parametrize('legacy', [False, True])
def test_comment_restart_requires_inspection_before_any_new_dispatch(tmp_path, legacy):
    queue, root, _, runs, _ = waiting_queue(tmp_path, legacy=legacy)
    assert queue.begin_comment_run(runs[0], 1)
    reopened = watcher.IssueQueue(root)
    assert reopened.mark_interrupted() == 1
    assert not reopened.begin_comment_run(runs[0], 1)
    assert not reopened.begin_comment_run(runs[1], 1)
    row = next(row for row in reopened.list() if row['run_id'] == runs[0])
    reopened.acknowledge(row['issue_key'], 'synthetic old worker confirmed stopped')
    assert reopened.begin_comment_run(runs[1], 1)


@pytest.mark.parametrize('damage', ['wrong_source', 'duplicate_binding'])
def test_comment_run_rejects_inconsistent_binding_without_mutating_rows(tmp_path, damage):
    queue, root, _, runs, _ = waiting_queue(tmp_path, legacy=True, cross_source=True)
    with closing(queue.store.connect()) as db, db:
        if damage == 'wrong_source':
            db.execute('UPDATE issue_queue SET url=? WHERE run_id=?',
                       ('https://github.com/owner/repo/issues/1', runs[1]))
        else:
            db.execute('UPDATE issue_queue SET run_id=? WHERE run_id=?', (runs[1], runs[0]))
    before = queue.list()
    with pytest.raises(StateError, match='queue_run_'):
        queue.begin_comment_run(runs[1], 2)
    with pytest.raises(StateError, match='queue_run_'):
        queue.finish_run(runs[1], 'awaiting_approval')
    assert queue.list() == before


def test_unqueued_manual_comment_run_has_no_queue_side_effect(tmp_path):
    _, _, root, _ = watch_setup(tmp_path)
    queue = watcher.IssueQueue(root)
    assert queue.begin_comment_run('manual-run', 1)
    queue.finish_run('manual-run', 'awaiting_approval')
    assert queue.list() == []
