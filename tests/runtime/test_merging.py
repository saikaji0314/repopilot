"""Native approval and merge recovery: real workflow/Git/SQLite, simulated GitHub HTTP."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from repopilot.delivery import merging
from repopilot.delivery.github import GitHub, publish
from repopilot.storage.jobs import JobStore, StateError
from tests.runtime.test_github import github_candidate, approve, ISSUE


MERGE_COMMIT = 'f' * 40


class Remote:
    def __init__(self, state, root):
        self.state, self.root = state, root
        self.head, self.base = state['reviewed_commit'], state['base_commit']
        publication = state['publication']
        self.pr = {'number': 2, 'html_url': 'https://github.com/owner/repo/pull/2',
                   'state': 'open', 'draft': False, 'merged': False, 'merge_commit_sha': None,
                   'user': {'login': 'delivery-bot'}, 'mergeable': True, 'mergeable_state': 'clean',
                   'head': {'sha': self.head, 'ref': publication['branch'], 'repo': {'full_name': 'owner/repo'}},
                   'base': {'sha': self.base, 'ref': 'main', 'repo': {'full_name': 'owner/repo'}}}
        self.protected = {'required_status_checks': {'strict': True, 'contexts': ['unit']},
                          'required_pull_request_reviews': {'required_approving_review_count': 1,
                                                           'dismiss_stale_reviews': True}}
        self.reviews = []
        self.runs = [{'id': 2, 'name': 'unit', 'status': 'completed', 'conclusion': 'success'}]
        self.statuses = []
        self.submit_count = self.result_count = 0
        self.finished = self.lose_response = self.submission_failed = False
        self.options_conflict = self.expired = False
        self.compare_status = 'ahead'
        self.client = GitHub('synthetic-secret', transport=httpx.MockTransport(self.handle))

    def native_approve(self, **overrides):
        review = {'id': 41, 'state': 'APPROVED', 'user': {'login': 'maintainer', 'type': 'User'},
                  'submitted_at': (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
                  'commit_id': self.head, 'html_url': self.pr['html_url'] + '#pullrequestreview-41'}
        self.reviews.append({**review, **overrides})

    def pending(self):
        return {'status': 'pending', 'details': {'uuid': 'request-123',
            'expected_head_sha': self.head, 'merge_method': 'squash' if self.options_conflict else 'merge',
            'merge_action': 'direct_merge', 'bypass_rules': False}}

    def handle(self, request):
        path = request.url.path
        prefix = '/repos/owner/repo'
        if path == prefix + '/pulls/2/merge-async' and request.method == 'PUT':
            self.submit_count += 1
            checkpoint = JobStore(self.root).get(self.state['run_id'])['merge']
            approvals=[x for x in self.reviews if x.get('state')=='APPROVED'
                       and (x.get('user') or {}).get('login')=='maintainer']
            assert checkpoint['status']=='submitting' and checkpoint['intent']['review']['id']==max(x['id'] for x in approvals)
            assert json.loads(request.content) == {'sha': self.head, 'merge_method': 'merge',
                                                   'merge_action': 'direct_merge', 'bypass_rules': False}
            assert request.headers['X-GitHub-Api-Version'] == '2026-03-10'
            if self.lose_response:
                self.lose_response = False
                raise httpx.ReadTimeout('synthetic-secret', request=request)
            return httpx.Response(409 if self.submit_count > 1 else 202, json=self.pending())
        if path == prefix + '/pulls/2/merge-async/request-123':
            self.result_count += 1
            if self.expired:
                return httpx.Response(404, json={'secret': 'synthetic-secret'})
            if self.submission_failed:
                return httpx.Response(200, json={'status': 'failed', 'details': {'message': 'synthetic-secret'}})
            if self.finished:
                self.pr.update(merged=True, state='closed', merge_commit_sha=MERGE_COMMIT,
                               merged_at=datetime.now(timezone.utc).isoformat())
                self.base = MERGE_COMMIT
                return httpx.Response(200, json={'status': 'merged', 'details': {'sha': MERGE_COMMIT}})
            return httpx.Response(200, json=self.pending())
        if path == prefix + '/pulls/2':
            return httpx.Response(200, json=self.pr)
        if path == prefix + '/pulls/2/reviews':
            return httpx.Response(200, json=self.reviews)
        if path == prefix + '/issues/1':
            return httpx.Response(200, json={'number': 1, 'html_url': ISSUE['url'], 'state': 'open',
                                            'title': ISSUE['title'], 'body': ISSUE['body']})
        if path == prefix:
            return httpx.Response(200, json={'full_name': 'owner/repo', 'default_branch': 'main'})
        if path == prefix + '/commits/main':
            return httpx.Response(200, json={'sha': self.base})
        if path == prefix + '/branches/main/protection':
            return httpx.Response(200, json=self.protected)
        if path.endswith('/check-runs'):
            return httpx.Response(200, json={'check_runs': self.runs})
        if path.endswith('/statuses'):
            return httpx.Response(200, json=self.statuses)
        if path.startswith(prefix + '/compare/'):
            return httpx.Response(200, json={'status': self.compare_status})
        raise AssertionError((request.method, path))


@pytest.fixture
def candidate(tmp_path):
    state, root, publisher = github_candidate(tmp_path)
    approve(state, root)
    publish(state['run_id'], root, publisher)
    state = JobStore(root).get(state['run_id'])
    remote = Remote(state, root)
    yield state, root, remote
    remote.client.close()


def start(candidate):
    state, root, remote = candidate
    merging.enable(state['run_id'], root, remote.client, owner='maintainer')
    return state, root, remote


def tick(candidate):
    state, root, remote = candidate
    return merging.step(state['run_id'], root, remote.client)['merge']


def test_approval_to_merge_requires_remote_confirmation_and_is_idempotent(candidate):
    state, root, remote = start(candidate)
    before = dict(state['control'])
    assert tick(candidate)['reason'] == 'native_owner_approval_required'
    assert remote.submit_count == 0
    remote.native_approve()
    assert tick(candidate)['status'] == 'merging'
    assert JobStore(root).get(state['run_id'])['status'] == 'published'
    assert tick(candidate)['status'] == 'merging'
    remote.finished = True
    result = tick(candidate)
    assert result['status'] == 'merged' and result['commit'] == MERGE_COMMIT
    final = JobStore(root).get(state['run_id'])
    assert final['status'] == 'merged' and final['control'] == before
    assert tick(candidate)['status'] == 'merged' and remote.submit_count == 1
    assert publish(state['run_id'], root, remote.client)['pull_request']['url'] == state['pull_request']['url']


@pytest.mark.parametrize('change,reason', [
    ({'commit_id': 'a'*40}, 'owner_approval_is_stale'),
    ({'user': {'login': 'stranger', 'type': 'User'}}, 'native_owner_approval_required'),
    ({'user': {'login': 'maintainer', 'type': 'Bot'}}, 'native_owner_approval_required'),
    ({'state': 'DISMISSED'}, 'native_owner_approval_required'),
    ({'state': 'CHANGES_REQUESTED'}, 'changes_requested'),
    ({'submitted_at': '2020-01-01T00:00:00Z'}, 'new_owner_approval_required'),
])
def test_invalid_native_approvals_never_merge(candidate, change, reason):
    _, _, remote = start(candidate)
    remote.native_approve(**change)
    assert tick(candidate)['reason'] == reason and remote.submit_count == 0


def test_latest_dismissal_blocks_but_comment_does_not_replace_approval(candidate):
    _, _, remote = start(candidate)
    remote.native_approve()
    remote.native_approve(id=42, state='COMMENTED')
    assert tick(candidate)['status'] == 'merging'


@pytest.mark.parametrize('mutation,reason', [
    ('head', 'pull_head_changed'), ('base', 'base_changed_revalidation_required'),
    ('draft', 'pull_is_draft'), ('conflict', 'pull_not_mergeable'),
    ('ci', 'required_ci_not_successful'), ('missing_ci', 'required_ci_not_successful'),
    ('status_failure', 'required_ci_not_successful'), ('other_reviewer', 'changes_requested'),
    ('local_change', 'reviewed_tree_changed'),
])
def test_changed_evidence_blocks_all_writes(candidate, mutation, reason):
    state, root, remote = start(candidate)
    remote.native_approve()
    if mutation == 'head':
        remote.pr['head']['sha'] = 'a'*40
    elif mutation == 'base':
        remote.base = 'b'*40
    elif mutation == 'draft':
        remote.pr['draft'] = True
    elif mutation == 'conflict':
        remote.pr['mergeable'] = False
    elif mutation == 'ci':
        remote.runs[0]['conclusion'] = 'failure'
    elif mutation == 'missing_ci':
        remote.runs.clear()
    elif mutation == 'status_failure':
        remote.statuses.append({'id': 3, 'context': 'unit', 'state': 'failure'})
    elif mutation == 'other_reviewer':
        remote.native_approve(id=42, state='CHANGES_REQUESTED', user={'login': 'another', 'type': 'User'})
    else:
        path = root/'worktrees'/state['run_id']/'app.py'
        path.write_text('unexpected = True\n')
    result = tick(candidate)
    assert reason in result['reason'] and remote.submit_count == 0


def test_same_author_and_unprotected_base_cannot_enable(candidate):
    state, root, remote = candidate
    remote.pr['user']['login'] = 'maintainer'
    with pytest.raises(StateError, match='owner_is_pull_author'):
        merging.enable(state['run_id'], root, remote.client, owner='maintainer')
    remote.pr['user']['login'] = 'delivery-bot'
    remote.protected['required_status_checks']['strict'] = False
    with pytest.raises(StateError, match='strict_branch_checks_required'):
        merging.enable(state['run_id'], root, remote.client, owner='maintainer')
    remote.protected['required_status_checks']['strict'] = True
    remote.protected['required_pull_request_reviews']['dismiss_stale_reviews'] = False
    with pytest.raises(StateError, match='protected_reviews_required'):
        merging.enable(state['run_id'], root, remote.client, owner='maintainer')
    assert not JobStore(root).get(state['run_id']).get('merge')


def test_lost_response_reconciles_same_request_without_false_success(candidate):
    _, _, remote = start(candidate)
    remote.native_approve()
    remote.lose_response = True
    result = tick(candidate)
    assert result['status'] == 'unknown' and 'synthetic-secret' not in json.dumps(result)
    assert tick(candidate)['request_id'] == 'request-123'  # HTTP 409 recovers existing operation.
    remote.finished = True
    assert tick(candidate)['status'] == 'merged'
    assert remote.submit_count == 2


def test_disable_stops_submissions_but_reconciles_accepted_merge(candidate):
    state, root, remote = start(candidate)
    merging.disable(state['run_id'], root)
    remote.native_approve()
    tick(candidate)
    assert remote.submit_count == 0


def test_disable_after_submission_preserves_recovery(candidate):
    state, root, remote = start(candidate)
    remote.native_approve()
    tick(candidate)
    merging.disable(state['run_id'], root)
    remote.finished = True
    assert tick(candidate)['status'] == 'merged' and remote.submit_count == 1


@pytest.mark.parametrize('failure,reason', [('expired', 'merge_request_expired_requires_review'),
                                         ('submission_failed', 'remote_merge_failed')])
def test_failed_or_expired_request_is_not_resubmitted(candidate, failure, reason):
    _, _, remote = start(candidate)
    remote.native_approve()
    tick(candidate)
    setattr(remote, failure, True)
    result = tick(candidate)
    assert result['reason'] == reason
    tick(candidate)
    assert remote.submit_count == 1


def test_confirmation_checks_commit_ancestry_not_just_success_flag(candidate):
    _, _, remote = start(candidate)
    remote.native_approve()
    tick(candidate)
    remote.finished = True
    remote.compare_status = 'diverged'
    assert tick(candidate)['reason'] == 'merged_candidate_not_contained'


def test_different_async_merge_options_do_not_get_adopted(candidate):
    _, _, remote = start(candidate)
    remote.native_approve()
    remote.options_conflict = True
    assert tick(candidate)['reason'] == 'merge_request_options_conflict'


def test_cli_local_merge_show_and_disable_need_no_credentials(candidate, monkeypatch, capsys):
    import repopilot.cli as cli
    state, root, _ = start(candidate)
    monkeypatch.setattr(cli, 'load_settings', lambda: pytest.fail('no model credentials'))
    assert cli.main(['merge', '--output', str(root), 'show', state['run_id']]) == 0
    assert json.loads(capsys.readouterr().out)['merge']['status'] == 'waiting_approval'
    assert cli.main(['merge', '--output', str(root), 'disable', state['run_id']]) == 0


def test_merge_report_escapes_untrusted_values(candidate):
    from repopilot.presentation.sections import merge_result
    state, root, _ = start(candidate)
    record = JobStore(root).get(state['run_id'])['merge']
    record['reason'] = '<script>bad()</script>'
    html = merge_result(record, root)
    assert '<script>' not in html and '&lt;script&gt;' in html


def test_local_policy_cannot_change_owner_or_reuse_old_draft_approval(candidate):
    state, root, remote = start(candidate)
    remote.native_approve()
    changed = JobStore(root).get(state['run_id'])
    changed['merge']['policy']['owner'] = 'stranger'
    JobStore(root).save(changed)
    assert tick(candidate)['reason'] == 'merge_policy_changed' and remote.submit_count == 0


def test_plain_human_answer_cannot_authorize_native_merge(candidate):
    from repopilot.orchestration import human
    state, root, remote = start(candidate)
    request = next(x for x in JobStore(root).get(state['run_id'])['human_requests'] if x['kind']=='merge_approval')
    with pytest.raises(StateError):
        human.answer(state['run_id'],root,request_id=request['id'],version=request['version'],text='同意')
    assert tick(candidate)['reason'] == 'native_owner_approval_required' and remote.submit_count == 0


def test_owner_review_is_saved_in_unified_human_request(candidate):
    state, root, remote = start(candidate)
    remote.native_approve()
    tick(candidate)
    request = next(x for x in JobStore(root).get(state['run_id'])['human_requests'] if x['kind']=='merge_approval')
    assert request['status']=='answered' and request['reply']['channel']=='github-review'
    assert request['reply']['source']['login']=='maintainer'
    assert request['reply']['source']['commit_id']==remote.head


def test_review_selection_and_ci_latest_results_do_not_ignore_newer_failures():
    policy={'owner':'maintainer','head':'a'*40,'enabled_at':'2026-01-01T00:00:00+00:00'}
    good={'id':1,'state':'APPROVED','user':{'login':'maintainer','type':'User'},'commit_id':'a'*40,
          'submitted_at':'2026-01-02T00:00:00Z'}
    with pytest.raises(StateError,match='native_owner_approval_required'):
        merging.owner_approval([good,{**good,'id':2,'state':'DISMISSED'}],policy)
    with pytest.raises(StateError,match='required_ci_not_successful'):
        merging.successful_checks([], [{'id':1,'context':'ci','state':'success'},
                                      {'id':2,'context':'ci','state':'pending'}], {'ci'})


def test_review_pagination_is_bounded_and_never_accepts_truncated_approval():
    from repopilot.delivery.github_merge import GitHubMerge
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json=[{'id':i} for i in range(100)])
    with closing(GitHub('synthetic',transport=httpx.MockTransport(handler))) as api:
        with pytest.raises(Exception,match='merge_observation_scan_limit'):
            GitHubMerge(api,'owner/repo',2).reviews()
    assert len(requests)==5


def test_cli_watch_once_automatically_submits_after_native_review(candidate, monkeypatch, capsys):
    import repopilot.cli as cli
    import repopilot.delivery.merge_cli as merge_cli
    state, root, remote = start(candidate)
    remote.native_approve()
    monkeypatch.setattr(merge_cli, 'open_forge', lambda _: remote.client)
    monkeypatch.setattr(cli, 'load_settings', lambda: pytest.fail('must not use model credentials'))
    assert cli.main(['merge','--output',str(root),'watch',state['run_id'],'--once'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['merge']['status']=='merging' and remote.submit_count==1


@pytest.mark.parametrize('repository,number',[('../user/other',2),('owner/repo',0),('owner/repo',True)])
def test_merge_adapter_rejects_arbitrary_api_paths(repository,number):
    from repopilot.delivery.github_merge import GitHubMerge
    from repopilot.delivery.forge import ForgeError
    with closing(GitHub('synthetic',transport=httpx.MockTransport(lambda _:pytest.fail('no requests')))) as api:
        with pytest.raises(ForgeError,match='invalid_merge_identity'):
            GitHubMerge(api,repository,number)
