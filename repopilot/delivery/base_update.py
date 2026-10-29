"""Bounded PR base updates using the same workflow, budget and review machinery."""

from copy import deepcopy

from repopilot.core.config import Task
from repopilot.delivery import merging
from repopilot.execution.repository import Repository
from repopilot.execution.workspace import head_commit, run_git, source_snapshot, WorkspaceError
from repopilot.orchestration.state import approval_digest, attach, canonical_hash
from repopilot.review import basis, merge as review_merge
from repopilot.storage.jobs import JobStore, StateError, job_lock, timestamp


def current(state):
    return ((state.get('merge') or {}).get('updates') or [None])[-1]


def active(state):
    update = current(state)
    return update is not None and update['status'] != 'published'


def request(state, store, target):
    merge = state['merge']; policy = merge['policy']
    merging.require(policy.get('auto_update') is True, 'base_changed_revalidation_required')
    merging.require(not merge.get('intent') and not merge.get('request_id') and merge['attempts'] == 0,
                    'pending_merge_requires_reconciliation')
    merging.require(len(merge.get('updates') or []) < policy.get('max_updates', 3), 'base_update_limit')
    merging.local_evidence(state, store.root)
    from repopilot.collaboration.requests import find_request, put_request
    prior = find_request(state, merge['human_request_id'])
    if prior.status == 'pending':
        put_request(state, prior.model_copy(update={'status': 'superseded'}))
    before = {key: deepcopy(state.get(key)) for key in (
        'base_commit', 'reviewed_commit', 'approval', 'approval_digest', 'verified', 'review_report',
        'report', 'publication', 'pull_request', 'require_approval', 'plan', 'plan_progress')}
    update = {'id': len(merge.get('updates') or []) + 1, 'status': 'requested',
              'from_head': state['reviewed_commit'], 'from_base': state['base_commit'],
              'remote_from_head': policy['head'], 'target_base': target,
              'requested_at': timestamp(), 'before': before, 'previous_policy': deepcopy(policy)}
    merge.setdefault('updates', []).append(update)
    merge.update(status='updating', reason='target_branch_advanced')
    merging.save(store, state, 'base_update_requested', update=update['id'], target=target)


def begin(state, store, forge):
    update = current(state); policy = state['merge']['policy']
    api, repository = merging.identity(state, forge)
    pull = api.pull(); merging.pull_identity(state, pull)
    merging.require(pull.get('state') == 'open' and not pull.get('merged'), 'open_pull_required')
    merging.require(canonical_hash(forge.issue(state['issue']['url'])) == canonical_hash(state['issue']),
                    'issue_changed_revalidation_required')
    metadata = forge.repository_metadata(repository)
    merging.require(metadata['full_name'].lower() == repository.lower()
                    and metadata['default_branch'] == policy['base_ref'], 'target_branch_changed')
    merging.local_evidence(state, store.root)
    fetched = forge.prepare_base(Task.model_validate(state['task']).repository, repository)
    if fetched != update['target_base']:
        update['status'] = 'superseded'
        request(state, store, fetched)
        return False
    workspace = attach(state, store.root).prepare()
    merging.require(run_git(workspace.path, 'merge-base', '--is-ancestor', update['from_base'], fetched,
                            check=False).returncode == 0, 'target_history_rewritten')
    # Validate changed upstream paths before a resolver can see their content.
    changed = run_git(workspace.path, 'diff', '--name-only', '-z', update['from_base'], fetched).stdout
    for path in changed.decode('utf-8').split('\0'):
        if path:
            try:
                Repository(workspace.path).resolve(path, must_exist=False)
            except ValueError:
                raise WorkspaceError('unsafe_upstream_path') from None
    state['base_commit'] = fetched
    basis.invalidate(state, 'pull_target_branch_updated')
    state['require_approval'] = False  # The new candidate needs a native merge review, not fake draft approval.
    if state.get('plan') and state['plan'] not in state.get('plan_history', []):
        state.setdefault('plan_history', []).append(deepcopy(state['plan']))
    state['plan'] = None  # The completed plan is historical; do not rerun its implementation.
    state['plan_refresh_required'] = False
    state.update(status='ready', phase='base_update', in_flight=None, expected_head=update['from_head'])
    state['workspace_snapshot'] = source_snapshot(workspace.path)
    update.update(status='integrating', started_at=timestamp())
    merging.save(store, state, 'base_update_started', update=update['id'])
    return True


def integrate(runner):
    """A normal persisted workflow phase; exceptions require existing explicit recovery."""
    from repopilot.orchestration import human, parallel
    state = runner.state; update = current(state); workspace = runner.workspace
    if update is None or update['status'] not in {'integrating', 'needs_input', 'committing'}:
        raise StateError('base_update_not_prepared')
    root = workspace.repository(); head = head_commit(root)
    pending = run_git(root, 'rev-parse', '-q', '--verify', 'MERGE_HEAD', check=False)
    if not pending.returncode:
        merging.require(pending.stdout.decode().strip() == update['target_base'], 'unexpected_merge_head')
    elif head == update['from_head']:
        update['merge_started'] = True; runner.checkpoint()
        result = run_git(root, '-c', 'core.hooksPath=', '-c', 'user.name=RepoPilot',
                         '-c', 'user.email=repopilot@localhost', 'merge', '--no-commit', '--no-ff',
                         update['target_base'], check=False)
        if result.returncode and not workspace.integration_conflicts():
            raise WorkspaceError('base_update_merge_failed')
    else:
        # A merge commit may have been written immediately before the process died.
        merging.require(all(run_git(root, 'merge-base', '--is-ancestor', source, head,
                                    check=False).returncode == 0
                            for source in (update['from_head'], update['target_base'])),
                        'unexpected_updated_head')
        workspace.assert_clean(head)

    conflicts = workspace.integration_conflicts()
    current(state)['conflicts'] = conflicts; runner.checkpoint()
    update = current(state)
    if conflicts and not (update.get('report', {}).get('status') == 'resolved'
                          and not workspace.unresolved_markers(conflicts)):
        payload = {'request': runner.task.request, 'task_brief': state.get('task_brief'),
                   'human_answers': state.get('human_answers') or [],
                   'previous_plan': update['before'].get('plan'),
                   'conflict': {'files': conflicts, 'source_commit': update['target_base'],
                                'base_commit': update['from_base'], 'candidate_commit': update['from_head']},
                   'note': '这是PR候选与最新主分支的冲突。保留双方业务行为；缺少业务决定时集中问负责人。'}
        runner._attach_memory(payload, 'integrate')
        with runner._implementation_slot('pull-base-update'):
            report = parallel.resolve_conflict(task=runner.task, settings=runner.settings,
                control=runner.control, trace=runner.trace, workspace=workspace, payload=payload,
                model=runner.model, edits=runner.edits, material_tools=runner._material_tools())
        current(state)['report'] = report; runner.checkpoint()
        update = current(state)
        if report['status'] == 'needs_clarification':
            update['status'] = 'needs_input'
            human.wait_for_clarification(state, runner.store_root, summary=report['summary'],
                questions=report.get('questions') or ['冲突需要确认应保留的业务行为。'],
                limit=runner.settings.max_question_batches,
                blocking={'phase': 'base_update'})
            runner.checkpoint(); return
        merging.require(report['status'] == 'resolved', 'base_update_resolution_blocked')
    markers = workspace.unresolved_markers(conflicts)
    merging.require(not markers, 'base_update_conflict_markers_remain')
    update['status'] = 'committing'; runner.checkpoint()
    merged = workspace.commit(f'chore: 更新PR主分支基线 {update["target_base"][:10]}')['commit']
    merging.require(not workspace.integration_conflicts(), 'base_update_index_conflicts_remain')
    merging.require(all(run_git(root, 'merge-base', '--is-ancestor', source, merged,
                                check=False).returncode == 0
                        for source in (update['from_head'], update['target_base'])), 'base_update_parent_missing')
    update = current(state)
    update.update(status='validating', integrated_commit=merged)
    state.setdefault('integrations', []).append({'kind': 'pull_base_update', 'update': update['id'],
        'source_commit': update['target_base'], 'base_commit': update['from_head'],
        'integrated_commit': merged, 'conflicts': conflicts, 'report': update.get('report'), 'at': timestamp()})
    state['reviewed_commit'] = state['expected_head'] = merged
    state['phase'] = 'verify'
    runner.trace.event('pull_base_updated', commit=merged, base_commit=update['target_base'])


def publish_updated(state, store, forge):
    """Only publish a newly verified/reviewed descendant, with an atomic old-head lease."""
    from repopilot.agents.loop import verification_passed
    update = current(state); policy = state['merge']['policy']
    api, repository = merging.identity(state, forge)
    task = Task.model_validate(state['task']); workspace = attach(state, store.root)
    from repopilot.orchestration import knowledge
    knowledge.validate(state, store.root)
    metadata = forge.repository_metadata(repository)
    merging.require(metadata['full_name'].lower() == repository.lower()
                    and metadata['default_branch'] == policy['base_ref'], 'target_branch_changed')
    merging.require(canonical_hash(forge.issue(state['issue']['url'])) == canonical_hash(state['issue']),
                    'issue_changed_revalidation_required')
    head = state['reviewed_commit']
    merging.require(state['status'] in {'completed', 'published'}, 'updated_review_incomplete')
    merging.require(verification_passed(task, state.get('verified') or [])
                    and not review_merge.approval_problems(state)
                    and (state.get('review_report') or {}).get('verdict') == 'approved', 'updated_review_incomplete')
    workspace.assert_clean(head)
    merging.require(all(run_git(workspace.path, 'merge-base', '--is-ancestor', source, head,
                                check=False).returncode == 0
                        for source in (update['from_head'], update['target_base'])), 'updated_candidate_not_descendant')
    target = forge.commit_sha(repository, policy['base_ref'])
    if target != update['target_base']:
        update.update(status='superseded', new_head=head, validation_digest=approval_digest(state))
        request(state, store, target)
        return
    pull = api.pull()
    merging.require(pull.get('state') == 'open' and not pull.get('merged'), 'open_pull_required')
    # Keep publication metadata untouched until both remote observations agree.
    observed = pull['head']['sha']
    remote_from_head = update.get('remote_from_head', update['from_head'])
    merging.require(observed in {remote_from_head, head}, 'updated_pull_head_conflict')
    merging.pull_identity(state, pull, expected_head=observed)
    update.update(status='pushing', new_head=head, validation_digest=approval_digest(state))
    merging.save(store, state, 'updated_push_intent', commit=head)
    if observed != head:
        merging.require(state['merge']['enabled'], 'merge_disabled')
        forge.push_update(workspace.path, repository, state['publication']['branch'], remote_from_head, head)
    after = api.pull()
    merging.require(after['head']['sha'] == head and
                    forge.branch_head(workspace.path, repository, state['publication']['branch']) == head,
                    'updated_push_not_confirmed')
    # Re-validate identity against a view of the intended new published candidate.
    merging.pull_identity(state, after, expected_head=head)
    state['publication']['commit'] = head
    state['pull_request'].update(commit=head, updated_at=timestamp())
    state['require_approval'] = bool(state['merge'].get('original_require_approval', update['before']['require_approval']))
    state.update(status='published', stop_reason='updated_pull_requires_native_approval', in_flight=None)
    policy.update(head=head, base=update['target_base'], approval_digest=approval_digest(state), enabled_at=timestamp())
    state['merge']['policy_hash'] = canonical_hash(policy)
    state['merge'].update(status='waiting_approval', reason='new_owner_approval_required', attempts=0)
    update.update(status='published', published_at=timestamp(), validation_digest=approval_digest(state))
    merging.open_merge_request(state)
    merging.save(store, state, 'updated_pull_published', commit=head, update=update['id'])


def advance(run_id, root, forge, settings, *, model=None):
    """Called outside merge.step's lock. Reuse original run counters, checks and dual review."""
    from repopilot.orchestration import workflow
    store = JobStore(root)
    with job_lock(store.root, run_id):
        state = store.get(run_id); update = current(state)
        merging.require(update is not None, 'base_update_not_requested')
        if update['status'] == 'published':
            return merging.summary(state)
        merging.require(state['merge']['enabled'] or update['status'] == 'pushing', 'merge_disabled')
        merging.require(canonical_hash(state['merge']['policy']) == state['merge']['policy_hash'], 'merge_policy_changed')
        if update['status'] == 'requested':
            if not begin(state, store, forge):
                return merging.summary(state)
        if state.get('in_flight') or state['status'] in {
                'needs_input', 'limit_reached', 'failed', 'workspace_error', 'phase_error',
                'review_inconclusive', 'rework_limit_reached', 'recovery_required'}:
            state['merge'].update(status='update_blocked', reason=state.get('stop_reason') or state['status'])
            merging.save(store, state, 'base_update_waiting', reason=state['merge']['reason'])
            return merging.summary(state)
        should_drive = update['status'] != 'pushing' and state['status'] in {'ready', 'paused', 'running'}
    if should_drive:
        workflow.resume(run_id, settings, store.root, model=model)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        merging.require(canonical_hash(state['merge']['policy']) == state['merge']['policy_hash'], 'merge_policy_changed')
        if state['status'] == 'completed' and not state.get('in_flight'):
            publish_updated(state, store, forge)
        else:
            state['merge'].update(status='update_blocked', reason=state.get('stop_reason') or state['status'])
            merging.save(store, state, 'base_update_waiting', reason=state['merge']['reason'])
        return merging.summary(state)
