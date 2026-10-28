"""监听准入、持久化去重与有界调度；模拟 HTTP 不等同真实监听演示。"""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from repopilot.core.config import RuntimeSettings, Task
from repopilot.delivery.github import GitHub, GitHubError
import repopilot.delivery.watcher as watcher
import repopilot.orchestration.workflow as workflow
from repopilot.storage.jobs import JobStore
from repopilot.storage.jobs import StateError
from repopilot.execution.workspace import run_git
from tests.runtime.test_review_loop import setup, FakeModel, review_approved
from tests.runtime.test_workflow import implementation


def queued_issue(number=1, *, created_at=None):
    return {"number": number, "html_url": f"https://github.com/owner/repo/issues/{number}", "state": "open",
            "created_at": created_at or datetime.now(timezone.utc).isoformat(), "labels": [{"name": "repopilot"}]}


def watch_setup(tmp_path):
    task, settings, root = setup(tmp_path)
    run_git(task.repository, 'remote', 'add', 'origin', 'https://github.com/owner/repo.git')
    path = tmp_path / 'task.yaml'
    import yaml
    path.write_text(yaml.safe_dump(task.model_dump(mode='json')), encoding='utf-8')
    config = watcher.WatchConfig(targets=[watcher.WatchTarget(task=path)])
    return task, settings, root, config


def test_new_issue_opt_in_and_restart_deduplication(tmp_path):
    task, settings, root, config = watch_setup(tmp_path)
    queue = watcher.IssueQueue(root)
    old = queued_issue(2, created_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    # 首次激活时间会截断到秒；给「新」Issue 留出余量，避免恰好跨秒时被当成旧工单。
    fresh = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    values = [queued_issue(created_at=fresh), old, queued_issue(3) | {'pull_request': {}},
              queued_issue(4) | {'state': 'closed'}, queued_issue(5) | {'labels': []}]
    class API:
        def open_issues(self, *args, **kwargs): return values
    assert watcher.poll_once(config, queue, API()) == 1
    assert watcher.poll_once(config, watcher.IssueQueue(root), API()) == 0
    assert [r['issue_key'] for r in queue.list()] == ['owner/repo#1']


def test_existing_job_is_not_automatically_retried(tmp_path):
    task, settings, root, config = watch_setup(tmp_path)
    JobStore(root).create({'run_id': 'failed-job', 'status': 'failed'}, issue_key='owner/repo#1')
    queue = watcher.IssueQueue(root)
    assert not queue.enqueue(queued_issue(), 'owner/repo', task, {})
    assert not queue.list()


def test_claim_limits_repository_and_keeps_interrupted_out_of_queue(tmp_path):
    task, settings, root, config = watch_setup(tmp_path)
    queue = watcher.IssueQueue(root)
    for number in (1, 2):
        assert queue.enqueue(queued_issue(number), 'owner/repo', task, {})
    first = queue.claim(1)
    assert first and queue.claim(1) is None
    assert queue.mark_interrupted() == 1
    assert queue.claim(1) is None
    queue.acknowledge(first['issue_key'], '已核查旧进程结束，保留现场待人工处理')
    second = queue.claim(1)
    assert second['issue_key'] != first['issue_key']
    queue.finish(second['issue_key'], 'awaiting_approval')
    assert queue.claim(1) is None
    assert {r['status'] for r in queue.list()} == {'acknowledged', 'awaiting_approval'}


def test_claim_allows_explicit_same_repository_parallelism(tmp_path):
    task, settings, root, config = watch_setup(tmp_path)
    queue = watcher.IssueQueue(root)
    for n in (1, 2, 3): queue.enqueue(queued_issue(n), 'owner/repo', task, {})
    assert queue.claim(2) and queue.claim(2)
    assert queue.claim(2) is None


def test_unknown_worker_blocks_all_dispatch_until_inspected(tmp_path):
    task, settings, root, config = watch_setup(tmp_path)
    queue = watcher.IssueQueue(root)
    queue.enqueue(queued_issue(), 'owner/repo', task, {})
    queue.claim(1)
    queue.bind('owner/repo#1', 'active-run')
    JobStore(root).create({'run_id': 'active-run', 'status': 'running'})
    queue.mark_interrupted()
    other = queued_issue(2) | {'html_url': 'https://github.com/owner/other/issues/2'}
    queue.enqueue(other, 'owner/other', task, {})
    assert queue.claim(4) is None
    with pytest.raises(StateError, match='reason_required'):
        queue.acknowledge('owner/repo#1', '')
    with pytest.raises(StateError, match='worker_still_running'):
        queue.acknowledge('owner/repo#1', '尚未确认退出')


def test_worker_binds_run_before_execution_and_releases_approval_wait(tmp_path, monkeypatch):
    task, settings, root, config = watch_setup(tmp_path)
    queue = watcher.IssueQueue(root)
    queue.enqueue(queued_issue(), 'owner/repo', task, {})
    item = queue.claim(1)
    class API:
        def __init__(self, token): pass
        def intake(self, url, template):
            return template, {'repository': 'owner/repo', 'number': 1, 'url': url, 'title': 'change', 'body': 'request'}
        def prepare_base(self, path, repo):
            from repopilot.execution.workspace import head_commit
            return head_commit(path)
        def close(self): pass
    original = workflow.resume
    def execute(run_id, *args, **kwargs):
        assert queue.list()[0]['run_id'] == run_id
        return original(run_id, *args, **kwargs, model=FakeModel(responses=[*implementation(), *review_approved()]))
    monkeypatch.setattr(watcher, 'open_forge', lambda *args, **kwargs: API('synthetic'))
    monkeypatch.setattr(watcher, 'load_settings', lambda: settings)
    monkeypatch.setattr(workflow, 'resume', execute)
    result = watcher.run_queued_job(root, item)
    assert result['status'] == 'awaiting_approval', result
    assert queue.list()[0]['status'] == 'awaiting_approval'
    assert queue.claim(1) is None


@pytest.mark.parametrize('settings', [{'api_key': 'secret'}, {'budget_cny': 999}, {'max_tool_calls': 999}, {'thinking': 'invalid'}])
def test_watch_config_cannot_supply_secrets_or_expand_budget(settings):
    with pytest.raises(ValidationError): watcher.WatchTarget(task=Path('task.yaml'), settings=settings)


def test_pagination_filters_prs():
    pages = []
    def handler(req):
        pages.append(req.url.params['page'])
        return httpx.Response(200, json=[queued_issue(n) for n in range(100)] if pages == ['1'] else [queued_issue(101) | {'pull_request': {'url': 'pr'}}])
    api = GitHub('synthetic', transport=httpx.MockTransport(handler))
    assert len(api.open_issues('owner/repo', label='repopilot')) == 100
    assert pages == ['1', '2']
    api.close()


def test_rate_limit_backoff_is_preserved():
    api = GitHub('synthetic', transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={'Retry-After': '90'})))
    with pytest.raises(GitHubError) as error: api.open_issues('owner/repo')
    assert error.value.retry_after == 90
    api.close()


def worker_probe(root, item):
    """供 spawn 子进程使用的探针；不调用 GitHub 或模型。"""
    import os
    import time
    from contextlib import closing
    queue = watcher.IssueQueue(root)
    with closing(queue.store.connect()) as db, db:
        db.execute('INSERT INTO worker_probes VALUES (?, ?)', (item['issue_key'], os.getpid()))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with closing(queue.store.connect()) as db:
            if db.execute('SELECT count(*) FROM worker_probes').fetchone()[0] == 2:
                queue.finish(item['issue_key'], 'completed')
                return {'issue_key': item['issue_key'], 'status': 'completed', 'pid': os.getpid()}
        time.sleep(0.05)
    raise RuntimeError('parallel_worker_timeout')


def test_dispatcher_uses_two_real_spawn_processes_for_two_repositories(tmp_path, monkeypatch):
    from contextlib import closing
    task, settings, root, config = watch_setup(tmp_path)
    config.max_workers = 2
    queue = watcher.IssueQueue(root)
    with closing(queue.store.connect()) as db, db:
        db.execute('CREATE TABLE worker_probes (issue_key TEXT PRIMARY KEY, pid INTEGER)')
    queue.enqueue(queued_issue(), 'owner/repo', task, {})
    other = queued_issue(2) | {'html_url': 'https://github.com/owner/other/issues/2'}
    queue.enqueue(other, 'owner/other', task, {})
    class API:
        def __init__(self, token): pass
        def open_issues(self, *args, **kwargs): return []
        def close(self): pass
    monkeypatch.setattr(watcher, 'open_forge', lambda *args, **kwargs: API('synthetic'))
    monkeypatch.setattr(watcher, 'run_queued_job', worker_probe)
    result = watcher.watch(config, root, once=True)
    assert result['status'] == 'completed'
    assert {row['status'] for row in result['queue']} == {'completed'}
    with closing(queue.store.connect()) as db:
        assert db.execute('SELECT count(DISTINCT pid) FROM worker_probes').fetchone()[0] == 2


def test_comment_resume_occupies_the_repository_slot(tmp_path):
    task, settings, root, config = watch_setup(tmp_path)
    queue = watcher.IssueQueue(root)
    now = datetime.now(timezone.utc).isoformat()

    def insert(key, repository, status):
        with closing(queue.store.connect()) as db, db:
            db.execute("INSERT INTO issue_queue VALUES (?,?,?,?,?,?,?,?)",
                       (key, repository, f"https://github.com/{key.replace('#', '/issues/')}",
                        json.dumps({}), status, None, None, now))

    insert("owner/repo#1", "owner/repo", "needs_input")
    insert("owner/repo#2", "owner/repo", "needs_input")
    insert("owner/repo#3", "owner/repo", "queued")
    assert queue.begin_comment("owner/repo#1", 1) is True
    assert queue.begin_comment("owner/repo#2", 1) is False, "评论恢复不能绕过同仓库上限"
    assert queue.claim(1) is None, "评论恢复占用的仓库不能同时认领新Issue"
    queue.restore_comment("owner/repo#1")
    assert queue.begin_comment("owner/repo#2", 1) is True
    queue.restore_comment("owner/repo#2")
    claimed = queue.claim(1)
    assert claimed and claimed["issue_key"] == "owner/repo#3"


def test_stop_signal_does_not_dispatch_pending_issues(tmp_path, monkeypatch):
    task, settings, root, config = watch_setup(tmp_path)
    queue = watcher.IssueQueue(root)
    queue.enqueue(queued_issue(), 'owner/repo', task, {})
    class API:
        def __init__(self, token): pass
        def open_issues(self, *args, **kwargs):
            (root / 'watcher.stop').write_text('stop', encoding='ascii')
            return []
        def close(self): pass
    monkeypatch.setattr(watcher, 'open_forge', lambda *args, **kwargs: API('synthetic'))
    monkeypatch.setattr(watcher, 'run_queued_job', worker_probe)
    result = watcher.watch(config, root)
    assert result['status'] == 'paused'
    assert queue.health()['status'] == 'stopped'
    assert queue.list()[0]['status'] == 'queued'
