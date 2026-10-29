"""PR refresh: real branch histories/workflow/SQLite and simulated GitHub/model outputs."""

from contextlib import contextmanager

import pytest

from repopilot.delivery import base_update, merging
from repopilot.core.config import RuntimeSettings
from repopilot.execution.workspace import run_git, head_commit, source_snapshot
from repopilot.orchestration import workflow, human
from repopilot.storage.jobs import JobStore
from tests.runtime.test_merging import candidate
from tests.runtime.test_review_loop import FakeModel, turn, review_approved


@contextmanager
def prepared(tmp_path, *, check=None, max_calls=36):
    from tests.runtime.test_review_loop import setup
    from tests.runtime.test_github import FakeGitHub,ISSUE,approve,publish
    from tests.runtime.test_workflow import implementation
    from tests.runtime.test_merging import Remote
    task,settings,root=setup(tmp_path,max_model_calls=max_calls)
    if check is not None:
        task=task.model_copy(update={'checks':{'unit':check}})
    run_git(task.repository,'remote','add','origin','https://github.com/owner/repo.git')
    result=workflow.start(task,settings,root,issue=ISSUE,
        model=FakeModel(responses=[*implementation(),*review_approved()]))
    assert result['status']=='awaiting_approval'
    state=JobStore(root).get(result['run_id']);approve(state,root)
    publish(state['run_id'],root,FakeGitHub(state))
    state=JobStore(root).get(state['run_id']);remote=Remote(state,root)
    try:
        yield state,root,remote
    finally:
        remote.client.close()


def upstream(candidate, *, conflict=False, empty=False):
    state,root,remote=candidate
    source=workflow.attach(state,root).source
    file=source/('app.py' if conflict else 'upstream.txt')
    if not empty:
        file.write_text('value = 10\n' if conflict else 'upstream change\n')
    run_git(source,'add','--all')
    run_git(source,'-c','user.name=Developer','-c','user.email=dev@example.test','commit','--allow-empty','-m','upstream change')
    target=head_commit(source)
    remote.base=target;remote.pr['base']['sha']=target
    remote.client.prepare_base=lambda *_:remote.base
    pushed=[]
    def update(path,repository,branch,previous,commit):
        assert remote.pr['head']['sha'] in {previous,commit}
        assert run_git(path,'merge-base','--is-ancestor',previous,commit,check=False).returncode==0
        pushed.append(commit)
        remote.head=commit;remote.pr['head']['sha']=commit
    remote.client.push_update=update
    remote.client.branch_head=lambda *_:remote.pr['head']['sha']
    return target,pushed,source


def enabled(candidate):
    state,root,remote=candidate
    merging.enable(state['run_id'],root,remote.client,owner='maintainer',auto_update=True)
    return state,root,remote


def advance(candidate,responses):
    state,root,remote=candidate
    settings=RuntimeSettings.model_validate({**state['settings'],'api_key':'synthetic'})
    return base_update.advance(state['run_id'],root,remote.client,settings,
                               model=FakeModel(responses=responses))


def test_clean_update_rechecks_reviews_pushes_once_and_requires_new_native_approval(candidate):
    state,root,remote=enabled(candidate)
    target,pushed,source=upstream(candidate)
    before=source_snapshot(source)
    remote.native_approve()  # This review belongs to the old head and must not authorize the new one.
    result=merging.step(state['run_id'],root,remote.client)
    assert result['merge']['status']=='updating'
    result=advance(candidate,review_approved())
    assert result['merge']['status']=='waiting_approval',result
    latest=JobStore(root).get(state['run_id']); update=base_update.current(latest)
    assert update['status']=='published' and len(pushed)==1
    assert latest['base_commit']==target and latest['approval'] is None
    assert latest['reviewed_commit']!=state['reviewed_commit']
    assert latest['control']['model_calls']==state['control']['model_calls']+2
    assert source_snapshot(source)==before
    assert merging.step(state['run_id'],root,remote.client)['merge']['reason']=='owner_approval_is_stale'
    remote.native_approve(id=42)
    assert merging.step(state['run_id'],root,remote.client)['merge']['status']=='merging'
    assert remote.submit_count==1
    assert update['before']['approval']['commit']==state['reviewed_commit']


def test_conflict_resolution_uses_business_context_and_preserves_both_parents(candidate):
    state,root,remote=enabled(candidate)
    target,pushed,source=upstream(candidate,conflict=True)
    merging.step(state['run_id'],root,remote.client)
    responses=[turn('write_file',{'path':'app.py','content':'value = 2\nupstream_value = 10\n'}),
        turn('IntegrationReport',{'status':'resolved','summary':'保留工单值与主线新增值','questions':[]}),
        *review_approved()]
    result=advance(candidate,responses)
    assert result['merge']['status']=='waiting_approval',result
    latest=JobStore(root).get(state['run_id']);path=workflow.attach(latest,root).path
    assert 'upstream_value = 10' in (path/'app.py').read_text()
    assert len(pushed)==1 and head_commit(source)==target
    assert run_git(path,'merge-base','--is-ancestor',target,latest['reviewed_commit'],check=False).returncode==0


def test_business_ambiguity_waits_without_pushing_and_answer_resumes_same_update(candidate):
    state,root,remote=enabled(candidate)
    target,pushed,_=upstream(candidate,conflict=True)
    merging.step(state['run_id'],root,remote.client)
    result=advance(candidate,[turn('IntegrationReport',{'status':'needs_clarification',
        'summary':'两边数值的业务含义不明确','questions':['两个数值应分别保存吗？']})])
    waiting=JobStore(root).get(state['run_id'])
    assert waiting['status']=='needs_input' and not pushed
    request=next(x for x in waiting['human_requests'] if x['kind']=='clarification' and x['status']=='pending')
    human.answer(state['run_id'],root,request_id=request['id'],version=request['version'],text='分别保存，工单值仍为2。')
    result=advance(candidate,[turn('write_file',{'path':'app.py','content':'value = 2\nupstream_value = 10\n'}),
        turn('IntegrationReport',{'status':'resolved','summary':'已分别保存','questions':[]}),*review_approved()])
    assert result['merge']['status']=='waiting_approval',result
    latest=JobStore(root).get(state['run_id'])
    assert len(latest['merge']['updates'])==1 and latest['requirements_revision']==1 and len(pushed)==1


def test_unresolved_markers_and_limit_never_push(candidate):
    state,root,remote=enabled(candidate)
    upstream(candidate,conflict=True)
    merging.step(state['run_id'],root,remote.client)
    result=advance(candidate,[turn('IntegrationReport',{'status':'resolved','summary':'宣称解决但没有改文件','questions':[]})])
    latest=JobStore(root).get(state['run_id'])
    assert latest['status']=='failed' and result['merge']['status']=='update_blocked'
    assert remote.pr['head']['sha']==state['reviewed_commit'] and remote.submit_count==0


def test_push_response_loss_reuses_new_candidate_without_repeating_review(candidate):
    state,root,remote=enabled(candidate)
    _,pushed,_=upstream(candidate)
    merging.step(state['run_id'],root,remote.client)
    original=remote.client.push_update
    def lost(*args):
        original(*args)
        from repopilot.delivery.forge import ForgeError
        raise ForgeError('github_update_push_unknown_reconcile_before_retry')
    remote.client.push_update=lost
    with pytest.raises(Exception,match='github_update_push_unknown'):
        advance(candidate,review_approved())
    before=JobStore(root).get(state['run_id'])['control']['model_calls']
    remote.client.push_update=lambda path,repo,branch,old,new:None
    result=advance(candidate,review_approved())
    assert result['merge']['status']=='waiting_approval'
    assert JobStore(root).get(state['run_id'])['control']['model_calls']==before and len(pushed)==1


def test_second_upstream_update_reuses_same_run_without_resetting_budget(candidate):
    state,root,remote=enabled(candidate)
    _,pushed,source=upstream(candidate)
    merging.step(state['run_id'],root,remote.client)
    advance(candidate,review_approved())
    first=JobStore(root).get(state['run_id'])
    (source/'second.txt').write_text('another upstream change\n')
    run_git(source,'add','--all')
    run_git(source,'-c','user.name=Developer','-c','user.email=dev@example.test','commit','-m','next upstream')
    remote.base=head_commit(source);remote.pr['base']['sha']=remote.base
    assert merging.step(state['run_id'],root,remote.client)['merge']['status']=='updating'
    result=advance(candidate,review_approved())
    assert result['merge']['status']=='waiting_approval',result
    latest=JobStore(root).get(state['run_id'])
    assert len(latest['merge']['updates'])==2 and len(pushed)==2
    assert latest['control']['model_calls']==first['control']['model_calls']+2


def test_interruption_after_merge_commit_resumes_without_remerging(candidate,monkeypatch):
    from repopilot.execution.workspace import Workspace
    state,root,remote=enabled(candidate)
    _,pushed,_=upstream(candidate)
    merging.step(state['run_id'],root,remote.client)
    original=Workspace.commit
    def interrupted(self,*args):
        original(self,*args)
        raise RuntimeError('simulated process interruption after commit')
    monkeypatch.setattr(Workspace,'commit',interrupted)
    advance(candidate,review_approved())
    interrupted_state=JobStore(root).get(state['run_id'])
    assert interrupted_state['in_flight']=='base_update' and not pushed
    committed=head_commit(workflow.attach(interrupted_state,root).path)
    workflow.recover(state['run_id'],root,commit=committed,reason='verified simulated interrupted merge commit')
    monkeypatch.setattr(Workspace,'commit',original)
    result=advance(candidate,review_approved())
    assert result['merge']['status']=='waiting_approval',result
    assert pushed==[committed]


def test_failed_independent_check_cannot_be_overridden_by_approved_reviews(tmp_path):
    from repopilot.core.config import Check
    check=Check(argv=['{python}','-c',"from pathlib import Path; assert not Path('upstream.txt').exists()"])
    with prepared(tmp_path,check=check) as candidate:
        state,root,remote=enabled(candidate)
        upstream(candidate)
        merging.step(state['run_id'],root,remote.client)
        result=advance(candidate,review_approved())
        latest=JobStore(root).get(state['run_id'])
        assert result['merge']['status']=='update_blocked'
        assert latest['verified'][0]['exit_code']==1
        assert remote.pr['head']['sha']==state['reviewed_commit']


def test_refresh_does_not_reset_exhausted_model_allowance(tmp_path):
    with prepared(tmp_path,max_calls=4) as candidate:
        state,root,remote=enabled(candidate)
        upstream(candidate)
        merging.step(state['run_id'],root,remote.client)
        result=advance(candidate,review_approved())
        latest=JobStore(root).get(state['run_id'])
        assert latest['status']=='limit_reached' and result['merge']['status']=='update_blocked'
        assert latest['control']['model_calls']==state['control']['model_calls']
        assert remote.pr['head']['sha']==state['reviewed_commit']


def test_tree_unchanged_update_still_creates_real_merge_parent(candidate):
    state,root,remote=enabled(candidate)
    target,pushed,_=upstream(candidate,empty=True)
    merging.step(state['run_id'],root,remote.client)
    result=advance(candidate,review_approved())
    assert result['merge']['status']=='waiting_approval',result
    latest=JobStore(root).get(state['run_id']);path=workflow.attach(latest,root).path
    parents=run_git(path,'show','-s','--format=%P',latest['reviewed_commit']).stdout.decode().split()
    assert parents==[state['reviewed_commit'],target] and len(pushed)==1


def test_lease_push_rejects_concurrent_writer_and_never_targets_main(tmp_path,monkeypatch):
    from tests.runtime.test_review_loop import make_repo
    from repopilot.delivery.github import GitHub
    from repopilot.delivery.forge import ForgeError
    import repopilot.delivery.github as module
    repo=make_repo(tmp_path);old=head_commit(repo)
    (repo/'app.py').write_text('value = 2\n')
    run_git(repo,'add','--all');run_git(repo,'commit','-m','candidate')
    new=head_commit(repo);remote=tmp_path/'remote.git';run_git(repo,'init','--bare',str(remote))
    branch='feature-repopilot-lease-test'
    run_git(repo,'push',str(remote),f'{old}:refs/heads/{branch}')
    api=GitHub('synthetic')
    monkeypatch.setattr(api,'branch_head',lambda *_:old)
    actual=module.run_git
    competing=run_git(repo,'-c','user.name=Other','-c','user.email=other@example.test',
        'commit-tree',old+'^{tree}','-p',old,'-m','concurrent writer').stdout.decode().strip()
    run_git(repo,'push',str(remote),f'{competing}:refs/heads/other')
    def race(path,*args,**kwargs):
        if 'push' in args:
            run_git(remote,'update-ref','refs/heads/'+branch,competing)
            args=tuple(str(remote) if x=='https://github.com/owner/repo.git' else x for x in args)
        return actual(path,*args,**kwargs)
    monkeypatch.setattr(module,'run_git',race)
    try:
        with pytest.raises(ForgeError,match='github_update_push_unknown'):
            api.push_update(repo,'owner/repo',branch,old,new)
        assert run_git(remote,'rev-parse','refs/heads/'+branch).stdout.decode().strip()==competing
        with pytest.raises(ForgeError,match='invalid_update_branch'):
            api.push_update(repo,'owner/repo','main',old,new)
    finally:
        api.close()


def test_main_advancing_again_retargets_within_same_bounded_budget(candidate,monkeypatch):
    state,root,remote=enabled(candidate)
    _,pushed,source=upstream(candidate)
    merging.step(state['run_id'],root,remote.client)
    original=workflow.resume; calls=[]
    def moved_after_validation(*args,**kwargs):
        result=original(*args,**kwargs)
        if not calls:
            (source/'next.txt').write_text('newer main\n')
            run_git(source,'add','--all')
            run_git(source,'-c','user.name=Dev','-c','user.email=dev@example.test','commit','-m','main advances again')
            remote.base=head_commit(source);remote.pr['base']['sha']=remote.base
        calls.append(1)
        return result
    monkeypatch.setattr(workflow,'resume',moved_after_validation)
    assert advance(candidate,review_approved())['merge']['status']=='updating'
    assert not pushed
    result=advance(candidate,review_approved())
    assert result['merge']['status']=='waiting_approval',result
    latest=JobStore(root).get(state['run_id'])
    assert len(latest['merge']['updates'])==2 and len(pushed)==1
    assert latest['base_commit']==remote.base and latest['require_approval'] is True
    assert latest['control']['model_calls']==state['control']['model_calls']+4


def test_disable_during_validation_prevents_new_branch_push(candidate,monkeypatch):
    state,root,remote=enabled(candidate)
    _,pushed,_=upstream(candidate)
    merging.step(state['run_id'],root,remote.client)
    original=workflow.resume
    def disable_after_validation(*args,**kwargs):
        result=original(*args,**kwargs)
        merging.disable(state['run_id'],root)
        return result
    monkeypatch.setattr(workflow,'resume',disable_after_validation)
    with pytest.raises(Exception,match='merge_disabled'):
        advance(candidate,review_approved())
    assert not pushed and remote.pr['head']['sha']==state['reviewed_commit']
