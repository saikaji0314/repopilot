"""A19来源隔离与旧索引兼容：真实SQLite，未调用模型或平台。"""
from contextlib import closing

import pytest

from repopilot.delivery.forge import Origin
from repopilot.delivery.queue import IssueQueue
from repopilot.delivery import watcher
from repopilot.orchestration import workflow
from repopilot.storage.jobs import JobStore, StateError
from tests.runtime.test_review_loop import setup


URLS = ['https://github.com/owner/repo/issues/1',
        'https://gitlab.com/owner/repo/-/issues/1',
        'https://gitlab.example.test/owner/repo/-/issues/1']


def issue(url):
    return {'repository':'owner/repo','number':1,'url':url,'title':'same number','body':'request'}


def test_jobs_disambiguate_platform_host_project_and_reuse_only_own_source(tmp_path):
    task, settings, root = setup(tmp_path)
    records = [workflow.create_job(task,settings,root,issue=issue(url)) for url in URLS]
    assert len({state['run_id'] for state in records}) == 3
    for url, record in zip(URLS,records):
        assert workflow.create_job(task,settings,root,issue=issue(url))['run_id'] == record['run_id']


def legacy_job(task, settings, root, url):
    state=workflow.create_job(task,settings,root,issue=issue(url))
    state['control']={'model_calls':7,'accounted_cny':1.25}
    state['approval']={'commit':'a'*40,'approved':True,'digest':'saved'}
    state['status']='failed'
    store=JobStore(root)
    store.save(state)
    with closing(store.connect()) as db, db:
        db.execute('UPDATE jobs SET issue_key=? WHERE id=?',('owner/repo#1',state['run_id']))
    return state


def test_legacy_matching_job_migrates_index_without_losing_state_or_approval(tmp_path):
    task, settings, root=setup(tmp_path)
    old=legacy_job(task,settings,root,URLS[0])
    reused=workflow.create_job(task,settings,root,issue=issue(URLS[0]))
    assert reused==old
    with closing(JobStore(root).connect()) as db:
        rows=db.execute('SELECT issue_key FROM jobs').fetchall()
    assert rows==[('github:github.com/owner/repo#1',)]


def test_foreign_legacy_job_does_not_block_or_reuse_other_platform(tmp_path):
    task, settings, root=setup(tmp_path)
    old=legacy_job(task,settings,root,URLS[0])
    other=workflow.create_job(task,settings,root,issue=issue(URLS[1]))
    assert other['run_id']!=old['run_id']
    assert JobStore(root).get(old['run_id'])==old


def test_retry_of_legacy_job_keeps_history_and_rebinds_only_matching_identity(tmp_path):
    task, settings, root=setup(tmp_path)
    old=legacy_job(task,settings,root,URLS[0])
    new=workflow.create_job(task,settings,root,issue=issue(URLS[0]),retry_of=old['run_id'])
    assert new['run_id']!=old['run_id']
    assert JobStore(root).get(old['run_id'])==old
    assert workflow.create_job(task,settings,root,issue=issue(URLS[0]))['run_id']==new['run_id']
    with pytest.raises(StateError,match='issue_already_has_new_attempt'):
        workflow.create_job(task,settings,root,issue=issue(URLS[0]),retry_of=old['run_id'])


def test_legacy_job_without_origin_requires_inspection_instead_of_guessing(tmp_path):
    task,settings,root=setup(tmp_path)
    JobStore(root).create({'run_id':'unknown','status':'failed'},issue_key='owner/repo#1')
    with pytest.raises(StateError,match='legacy_issue_identity_requires_review'):
        workflow.create_job(task,settings,root,issue=issue(URLS[0]))


def test_queue_disambiguates_three_sources_and_preserves_old_queued_identity(tmp_path):
    task,_,root=setup(tmp_path)
    queue=IssueQueue(root)
    assert queue.enqueue({'number':1,'html_url':URLS[0]},'owner/repo',task,{})
    with closing(queue.store.connect()) as db,db:
        db.execute("UPDATE issue_queue SET issue_key='owner/repo#1'")
    assert not queue.enqueue({'number':1,'html_url':URLS[0]},'owner/repo',task,{})
    for url in URLS[1:]:
        assert queue.enqueue({'number':1,'html_url':url},'owner/repo',task,{})
    assert len(queue.list())==3
    assert 'owner/repo#1' in {row['issue_key'] for row in queue.list()}


def test_queue_legacy_job_suppression_checks_origin_and_comment_finishes_by_run(tmp_path):
    task,settings,root=setup(tmp_path)
    old=legacy_job(task,settings,root,URLS[0])
    queue=IssueQueue(root)
    assert not queue.enqueue({'number':1,'html_url':URLS[0]},'owner/repo',task,{})
    assert queue.enqueue({'number':1,'html_url':URLS[1]},'owner/repo',task,{})
    key=queue.list()[0]['issue_key']
    queue.bind(key,old['run_id'])
    queue.finish_run(old['run_id'],'awaiting_approval')
    assert queue.list()[0]['status']=='awaiting_approval'


def test_watch_activation_includes_source_host(tmp_path,monkeypatch):
    task,_,root=setup(tmp_path)
    config=watcher.WatchConfig(targets=[watcher.WatchTarget(task=tmp_path/'github.yaml'),
                                      watcher.WatchTarget(task=tmp_path/'gitlab.yaml')])
    monkeypatch.setattr(watcher,'load_task',lambda path:task.model_copy(update={'id':path.stem,'repository':path}))
    monkeypatch.setattr(watcher,'remote_origin',lambda path:Origin(
        'github' if path.stem=='github' else 'gitlab',
        'github.com' if path.stem=='github' else 'gitlab.com','owner/repo'))
    class API:
        def open_issues(self,*args,**kwargs): return []
    queue=IssueQueue(root)
    watcher.poll_once(config,queue,API())
    with closing(queue.store.connect()) as db:
        values={row[0] for row in db.execute('SELECT id FROM watch_targets')}
    assert values=={'github:github.com/owner/repo:repopilot','gitlab:gitlab.com/owner/repo:repopilot'}
