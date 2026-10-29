"""Opt-in native-owner approval -> checked GitHub merge -> observed confirmation.

The published candidate and original draft approval remain immutable. A separate
per-run policy grants only observation and merging after a new native owner review.
"""

from datetime import datetime
import re

from repopilot.collaboration.requests import find_request, open_request, put_request, resolve_request
from repopilot.delivery.forge import ForgeError, parse_issue_url
from repopilot.delivery.github_merge import GitHubMerge
from repopilot.execution.workspace import WorkspaceError
from repopilot.knowledge.documents import KnowledgeFault
from repopilot.orchestration.state import approval_digest, attach, canonical_hash
from repopilot.review.merge import approval_problems
from repopilot.storage.jobs import JobStore, StateError, job_lock, timestamp


class MergeBlocked(StateError):
    pass


def require(condition, reason):
    if not condition:
        raise MergeBlocked(reason)


def sha(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{40,64}', value) is not None


def identity(state, forge):
    source = parse_issue_url((state.get('issue') or {}).get('url'))
    require(source['provider'] == 'github' and source['host'] == 'github.com', 'github_merge_only')
    repository = source['repository']
    require(repository.lower() == state['issue']['repository'].lower(), 'issue_identity_mismatch')
    pull = state.get('pull_request') or {}
    number = pull.get('number')
    require(type(number) is int and number > 0, 'published_pull_required')
    require(pull.get('url') == f'https://github.com/{repository}/pull/{number}', 'pull_identity_mismatch')
    return GitHubMerge(forge, repository, number), repository


def local_evidence(state, root):
    from repopilot.orchestration import knowledge
    approval = state.get('approval') or {}
    require(approval.get('approved') and approval.get('commit') == state.get('reviewed_commit')
            and approval.get('digest') == approval_digest(state), 'matching_draft_approval_required')
    require(not approval_problems(state), 'review_evidence_incomplete')
    require(all(x.get('exit_code') == 0 and not x.get('timed_out') for x in state.get('verified') or [])
            and bool(state.get('verified')), 'independent_checks_required')
    workspace = attach(state, root)
    require(workspace is not None, 'workspace_required')
    workspace.assert_clean(approval['commit'])
    knowledge.validate(state, root)
    return approval['commit']


def pull_identity(state, pull):
    pub = state['publication']
    repository = state['issue']['repository'].lower()
    require(pull.get('number') == state['pull_request']['number']
            and pull.get('html_url') == state['pull_request']['url'], 'pull_identity_mismatch')
    for side in ('head', 'base'):
        require(str(((pull.get(side) or {}).get('repo') or {}).get('full_name', '')).lower() == repository,
                'pull_repository_changed')
    require(pull['head'].get('ref') == pub['branch'] and pull['base'].get('ref') == pub['base'],
            'pull_branch_changed')
    require(pull['head'].get('sha') == pub['commit'] == state['reviewed_commit'], 'pull_head_changed')


def strict_checks(api, branch, configured):
    protection = api.protection(branch)
    checks = protection.get('required_status_checks') or {}
    names = set(checks.get('contexts') or []) | {
        x.get('context') for x in checks.get('checks') or [] if x.get('context')}
    # Two client GETs cannot lock the remote base. Strict branch checks are the
    # platform-side gate for concurrent base movement; no rules/admin bypass.
    require(checks.get('strict') is True and bool(names), 'strict_branch_checks_required')
    reviews = protection.get('required_pull_request_reviews') or {}
    require(type(reviews.get('required_approving_review_count')) is int
            and reviews['required_approving_review_count'] >= 1
            and reviews.get('dismiss_stale_reviews') is True, 'protected_reviews_required')
    return names | set(configured)


def enable(run_id, root, forge, *, owner, required_checks=()):
    require(bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', owner)), 'invalid_merge_owner')
    require(len(required_checks) <= 40 and all(isinstance(x, str) and 0 < len(x) <= 150
            and not any(ord(c) < 32 for c in x) for x in required_checks), 'invalid_required_checks')
    store = JobStore(root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        require(state['status'] == 'published', 'published_run_required')
        require(not state.get('merge'), 'merge_policy_already_registered')
        api, repository = identity(state, forge)
        commit = local_evidence(state, store.root)
        pull = api.pull()
        pull_identity(state, pull)
        require(not pull.get('merged') and pull.get('state') == 'open', 'open_pull_required')
        require((pull.get('user') or {}).get('login', '').lower() != owner.lower(), 'owner_is_pull_author')
        metadata = forge.repository_metadata(repository)
        require(metadata['full_name'].lower() == repository.lower()
                and metadata['default_branch'] == state['publication']['base'], 'target_branch_changed')
        require(pull['base']['sha'] == state['base_commit']
                and forge.commit_sha(repository, state['publication']['base']) == state['base_commit'],
                'base_changed_revalidation_required')
        strict_checks(api, state['publication']['base'], required_checks)
        policy = {'owner': owner, 'required_checks': sorted(set(required_checks)),
                  'head': commit, 'base': state['base_commit'], 'base_ref': state['publication']['base'],
                  'approval_digest': approval_digest(state), 'enabled_at': timestamp(),
                  'method': 'merge', 'provider': 'github', 'repository': repository}
        state['merge'] = {'policy': policy, 'policy_hash': canonical_hash(policy), 'enabled': True,
                          'status': 'waiting_approval', 'reason': 'native_owner_approval_required',
                          'attempts': 0, 'events': []}
        request = open_request(state, kind='merge_approval',
            summary='等待登记负责人在GitHub批准当前候选后受控合并。',
            questions=['将草稿标为Ready for review，核对当前提交和CI后在GitHub提交Approve。'],
            context={'run_id': run_id, 'merge_policy': policy})
        put_request(state, request.model_copy(update={'phase': 'merge'}))
        state['merge']['human_request_id'] = request.id
        save(store, state, 'enabled')
        return summary(state)


def save(store, state, event, **values):
    merge = state['merge']
    previous = (merge.get('events') or [{}])[-1]
    if event in {'blocked', 'transport_or_api_error', 'request_observed'} and (
            {key: value for key, value in previous.items() if key != 'at'} == {'event': event, **values}):
        return
    merge['updated_at'] = timestamp()
    merge['events'].append({'at': merge['updated_at'], 'event': event, **values})
    store.save(state)


def summary(state):
    return {'run_id': state['run_id'], 'status': 'ok', 'merge': state.get('merge')}


def disable(run_id, root):
    store = JobStore(root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        require(bool(state.get('merge')), 'merge_not_enabled')
        state['merge']['enabled'] = False
        save(store, state, 'disabled')
        return summary(state)


def owner_approval(reviews, policy):
    effective = {}
    for review in sorted(reviews, key=lambda x: x.get('id', 0)):
        user = review.get('user') or {}
        if review.get('state') in {'APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'}:
            effective[str(user.get('login') or '').lower()] = review
    require(not any(x.get('state') == 'CHANGES_REQUESTED' for x in effective.values()),
            'changes_requested')
    selected = effective.get(policy['owner'].lower()) or {}
    require(selected.get('state') == 'APPROVED'
            and (selected.get('user') or {}).get('type') == 'User', 'native_owner_approval_required')
    require(selected.get('commit_id') == policy['head'], 'owner_approval_is_stale')
    try:
        submitted = datetime.fromisoformat(selected['submitted_at'].replace('Z', '+00:00'))
        enabled = datetime.fromisoformat(policy['enabled_at'])
        fresh = submitted >= enabled
    except (KeyError, ValueError, TypeError):
        fresh = False
    require(fresh, 'new_owner_approval_required')
    require(type(selected.get('id')) is int and selected['id'] > 0, 'invalid_owner_review')
    return {key: selected.get(key) for key in ('id', 'commit_id', 'submitted_at', 'html_url')}


def successful_checks(runs, statuses, required):
    observed = {}
    # Status API is newest-first; retain the latest status for each context.
    for item in sorted(statuses, key=lambda x: x.get('id', 0), reverse=True):
        observed.setdefault(('status', item.get('context')), item.get('state') == 'success')
    for item in sorted(runs, key=lambda x: x.get('id', 0), reverse=True):
        observed.setdefault(('check', item.get('name')), item.get('status') == 'completed'
                            and item.get('conclusion') == 'success')
    for name in required:
        values = [value for (_, context), value in observed.items() if context == name]
        require(bool(values) and all(values), 'required_ci_not_successful')
    return sorted(required)


def preflight(state, root, api, forge, repository):
    policy = state['merge']['policy']
    require(canonical_hash(policy) == state['merge']['policy_hash'], 'merge_policy_changed')
    require(local_evidence(state, root) == policy['head']
            and approval_digest(state) == policy['approval_digest'], 'local_evidence_changed')
    require(canonical_hash(forge.issue(state['issue']['url'])) == canonical_hash(state['issue']),
            'issue_changed_revalidation_required')
    pull = api.pull()
    pull_identity(state, pull)
    require(not pull.get('merged') and pull.get('state') == 'open', 'open_pull_required')
    require(pull.get('draft') is False, 'pull_is_draft')
    require((pull.get('user') or {}).get('login', '').lower() != policy['owner'].lower(),
            'owner_is_pull_author')
    metadata = forge.repository_metadata(repository)
    require(metadata['full_name'].lower() == repository.lower()
            and metadata['default_branch'] == policy['base_ref'], 'target_branch_changed')
    require(pull['base']['sha'] == policy['base']
            and forge.commit_sha(repository, policy['base_ref']) == policy['base'],
            'base_changed_revalidation_required')
    require(pull.get('mergeable') is True and pull.get('mergeable_state') == 'clean', 'pull_not_mergeable')
    required = strict_checks(api, policy['base_ref'], policy['required_checks'])
    approval = owner_approval(api.reviews(), policy)
    runs, statuses = api.checks(policy['head'])
    successful_checks(runs, statuses, required)
    # Re-observe approval, draft state and head/base after fetching CI evidence.
    latest = api.pull()
    pull_identity(state, latest)
    require(latest.get('draft') is False and latest.get('state') == 'open'
            and latest['base'].get('sha') == policy['base']
            and latest.get('mergeable') is True and latest.get('mergeable_state') == 'clean',
            'pull_changed_during_observation')
    require(forge.commit_sha(repository, policy['base_ref']) == policy['base'],
            'base_changed_revalidation_required')
    require(owner_approval(api.reviews(), policy)['id'] == approval['id'], 'owner_review_changed')
    return {'owner': policy['owner'], 'review': approval, 'head': policy['head'],
            'base': policy['base'], 'checks': sorted(required), 'at': timestamp()}


def confirm(state, store, api, pull, expected_merge=None):
    policy = state['merge']['policy']
    pull_identity(state, pull)
    require(pull.get('merged') is True, 'merge_not_confirmed')
    commit = pull.get('merge_commit_sha')
    require(sha(commit) and (expected_merge is None or commit == expected_merge), 'merge_commit_mismatch')
    for base in (policy['head'], policy['base']):
        require(api.compare(base, commit).get('status') in {'ahead', 'identical'}, 'merged_candidate_not_contained')
    require(api.compare(commit, policy['base_ref']).get('status') in {'ahead', 'identical'},
            'merge_not_in_target_branch')
    merge = state['merge']
    merge.update(status='merged', reason='remote_merge_confirmed', commit=commit,
                 merged_at=pull.get('merged_at'), confirmed_at=timestamp())
    state['status'], state['stop_reason'] = 'merged', 'remote_merge_confirmed'
    save(store, state, 'confirmed', commit=commit)


def step(run_id, root, forge):
    """One bounded tick; checkpoints precede writes, retries reconcile first."""
    store = JobStore(root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        merge = state.get('merge')
        require(bool(merge), 'merge_not_enabled')
        if merge['status'] == 'merged':
            return summary(state)
        api, repository = identity(state, forge)
        try:
            require(canonical_hash(merge['policy']) == merge['policy_hash'], 'merge_policy_changed')
            pull = api.pull()
            pull_identity(state, pull)
            if pull.get('merged'):
                require(bool(merge.get('intent')), 'external_merge_requires_review')
                confirm(state, store, api, pull)
                return summary(state)
            if merge.get('request_id'):
                try:
                    result = api.result(merge['request_id'])
                except ForgeError as exc:
                    if exc.status == 404:
                        raise MergeBlocked('merge_request_expired_requires_review') from None
                    raise
                result_status = result.get('status')
                if result_status == 'merged':
                    confirm(state, store, api, api.pull(), (result.get('details') or {}).get('sha'))
                elif result_status == 'pending':
                    merge.update(status='merging', reason='remote_request_pending')
                elif result_status == 'failed':
                    merge.update(status='failed', reason='remote_merge_failed')
                else:
                    raise MergeBlocked('unexpected_merge_result')
                save(store, state, 'request_observed', result=result_status)
                return summary(state)
            if not merge['enabled']:
                return summary(state)
            require(merge['status'] != 'failed', 'merge_failed_requires_review')
            require(merge['attempts'] < 3, 'merge_submission_limit_requires_review')
            intent = preflight(state, store.root, api, forge, repository)
            request = find_request(state, merge['human_request_id'])
            if request.status == 'pending':
                resolve_request(state, request, action='approve', text='GitHub负责人批准当前提交合并。',
                    actor='registered-owner', channel='github-review',
                    source={'login': intent['owner'], **intent['review']})
            merge.update(status='submitting', reason='native_owner_approved', intent=intent,
                         attempts=merge['attempts'] + 1)
            save(store, state, 'submission_intent', review_id=intent['review']['id'])
            result = api.submit(merge['policy']['head'])
            details = result.get('details') or {}
            if result.get('status') == 'merged':
                confirm(state, store, api, api.pull(), details.get('sha'))
            else:
                require(result.get('status') == 'pending'
                        and details.get('expected_head_sha') == merge['policy']['head']
                        and details.get('merge_method') == 'merge'
                        and details.get('merge_action') == 'direct_merge'
                        and details.get('bypass_rules') is False
                        and isinstance(details.get('uuid'), str)
                        and re.fullmatch(r'[A-Za-z0-9-]{1,100}', details['uuid']),
                        'merge_request_options_conflict')
                merge.update(request_id=details['uuid'], status='merging', reason='remote_request_accepted')
                save(store, state, 'request_accepted', request_id=details['uuid'])
        except (StateError, ForgeError, WorkspaceError, KnowledgeFault) as exc:
            # Do not copy response bodies / exception diagnostics into evidence.
            reason = str(exc)
            blocked = not isinstance(exc, ForgeError)
            merge.update(status='blocked' if blocked else 'unknown', reason=reason)
            merge['retry_after'] = getattr(exc, 'retry_after', 0)
            save(store, state, 'blocked' if blocked else 'transport_or_api_error', reason=reason)
        return summary(state)
