"""Explicit per-run opt-in and foreground native-review polling, without model credentials."""

from contextlib import closing
import json
import time

from repopilot.delivery.forge import open_forge
from repopilot.delivery import merging
from repopilot.core.config import load_settings
from repopilot.storage.jobs import JobStore


def configure(parser):
    commands = parser.add_subparsers(dest='merge_command', required=True)
    for name in ('enable', 'show', 'disable', 'watch', 'refresh'):
        command = commands.add_parser(name)
        command.add_argument('run_id')
        if name == 'enable':
            command.add_argument('--owner', required=True, help='批准合并的GitHub负责人登录名')
            command.add_argument('--check', action='append', default=[], help='额外必需CI名称，可重复')
            command.add_argument('--auto-update', action='store_true', help='允许更新PR基线、必要时解决冲突并重新检查双审；沿用原运行额度')
            command.add_argument('--max-updates', type=int, choices=[1, 2, 3], default=3)
        if name == 'watch':
            command.add_argument('--once', action='store_true', help='只核对一次；未完成不宣称合并成功')
            command.add_argument('--poll-seconds', type=int, default=30, choices=range(15, 301),
                                 metavar='15..300')


def execute(args):
    if args.merge_command == 'show':
        return merging.summary(JobStore(args.output).get(args.run_id))
    if args.merge_command == 'disable':
        return merging.disable(args.run_id, args.output)
    state = JobStore(args.output).get(args.run_id)
    with closing(open_forge((state.get('issue') or {}).get('url'))) as forge:
        if args.merge_command == 'enable':
            return merging.enable(args.run_id, args.output, forge, owner=args.owner, required_checks=args.check,
                                  auto_update=args.auto_update, max_updates=args.max_updates)
        if args.merge_command == 'refresh':
            return advance(args, forge, merging.step(args.run_id, args.output, forge), explicit=True)
        last = None
        delay = args.poll_seconds
        try:
            while True:
                result = merging.step(args.run_id, args.output, forge)
                result = advance(args, forge, result)
                merge = result['merge']
                visible = (merge['status'], merge.get('reason'), merge.get('request_id'), merge.get('commit'))
                if args.once:
                    return result
                if visible != last:
                    print(json.dumps({'run_id': args.run_id, 'merge_status': merge['status'],
                                      'reason': merge.get('reason')}, ensure_ascii=True), flush=True)
                    last = visible
                if merge['status'] in {'merged', 'failed', 'update_blocked'} or not merge['enabled']:
                    return result
                if merge['status'] == 'blocked' and merge.get('reason') not in {
                        'native_owner_approval_required', 'new_owner_approval_required',
                        'owner_approval_is_stale', 'changes_requested', 'pull_is_draft',
                        'pull_not_mergeable', 'required_ci_not_successful',
                        'pull_changed_during_observation', 'owner_review_changed'}:
                    return result
                delay = (max(min(900, delay * 2), merge.get('retry_after', 0))
                         if merge['status'] == 'unknown' else args.poll_seconds)
                time.sleep(delay)
        except KeyboardInterrupt:
            return {'run_id': args.run_id, 'status': 'paused',
                    'merge': JobStore(args.output).get(args.run_id).get('merge')}


def advance(args, forge, result, *, explicit=False):
    from repopilot.delivery import base_update
    if result['merge']['status'] == 'updating' or (explicit and
            base_update.active(JobStore(args.output).get(args.run_id))):
        try:
            return base_update.advance(args.run_id, args.output, forge, load_settings())
        except (merging.StateError, merging.ForgeError, merging.WorkspaceError, merging.KnowledgeFault) as exc:
            from repopilot.storage.jobs import job_lock
            store = JobStore(args.output)
            with job_lock(store.root, args.run_id):
                state = store.get(args.run_id)
                state['merge'].update(status='update_blocked', reason=str(exc))
                merging.save(store, state, 'base_update_error', reason=str(exc))
                return merging.summary(state)
    return result
