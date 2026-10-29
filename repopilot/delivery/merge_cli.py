"""Explicit per-run opt-in and foreground native-review polling, without model credentials."""

from contextlib import closing
import json
import time

from repopilot.delivery.forge import open_forge
from repopilot.delivery import merging
from repopilot.storage.jobs import JobStore


def configure(parser):
    commands = parser.add_subparsers(dest='merge_command', required=True)
    for name in ('enable', 'show', 'disable', 'watch'):
        command = commands.add_parser(name)
        command.add_argument('run_id')
        if name == 'enable':
            command.add_argument('--owner', required=True, help='批准合并的GitHub负责人登录名')
            command.add_argument('--check', action='append', default=[], help='额外必需CI名称，可重复')
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
            return merging.enable(args.run_id, args.output, forge, owner=args.owner, required_checks=args.check)
        last = None
        delay = args.poll_seconds
        try:
            while True:
                result = merging.step(args.run_id, args.output, forge)
                merge = result['merge']
                visible = (merge['status'], merge.get('reason'), merge.get('request_id'), merge.get('commit'))
                if args.once:
                    return result
                if visible != last:
                    print(json.dumps({'run_id': args.run_id, 'merge_status': merge['status'],
                                      'reason': merge.get('reason')}, ensure_ascii=True), flush=True)
                    last = visible
                if merge['status'] in {'merged', 'failed'} or not merge['enabled']:
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
