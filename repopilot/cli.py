"""命令解析与输出；各命令的应用操作由独立处理函数负责。"""

import argparse
from importlib.metadata import version
from contextlib import closing
import json
from pathlib import Path
import sys

from repopilot.core.config import PROJECT_ROOT, load_profile, load_settings, load_task
from repopilot.core.paths import default_runtime_root
from repopilot.orchestration import workflow as workflow
from repopilot.delivery.forge import ForgeError, open_forge, publish as forge_publish
from repopilot.storage.jobs import JobStore, StateError

COMMANDS = {'run', 'issue', 'resume', 'retry', 'status', 'jobs', 'report', 'approve', 'reject',
            'recover', 'publish', 'watch', 'memory', 'human', 'knowledge'}
SUCCESS_STATUSES = {'ok', 'completed', 'paused', 'needs_input', 'awaiting_approval', 'approved', 'published', 'rejected'}
STATUS_FIELDS = ('run_id', 'status', 'phase', 'in_flight', 'stop_reason', 'reviewed_commit',
                 'report', 'review_report', 'verified', 'issue', 'approval', 'pull_request',
                 'human_requests', 'requirements_revision', 'knowledge_snapshot', 'knowledge_history', 'decision_records',
                 'publication')


def _watch_options(parser):
    parser.add_argument('--config', type=Path)
    parser.add_argument('--once', action='store_true', help='只轮询一次并处理本轮队列')
    parser.add_argument('--status', action='store_true', help='查看持久化 Issue 队列，不调用模型或仓库平台')
    parser.add_argument('--stop', action='store_true', help='请求监听器停止接单，等待当前任务落盘后退出')
    parser.add_argument('--acknowledge', help='核查残留 worker 后确认一个 interrupted Issue 队列键')
    parser.add_argument('--reason')


def _execution_options(parser, name):
    parser.add_argument('--stop-after', choices=['prepare', 'implement', 'anchor', 'verify', 'review', 'rework'])
    if name == 'resume':
        return
    parser.add_argument('--profile', type=Path, help='执行限额与思考模式 YAML，不含凭证')
    parser.add_argument('--max-context-bytes', type=int)
    parser.add_argument('--max-tool-calls', type=int)
    parser.add_argument('--max-run-cost-cny', type=float)
    parser.add_argument('--memory', dest='memory_enabled', action=argparse.BooleanOptionalAction, default=None,
                        help='启用仓库事实检索；--no-memory关闭，新运行默认关闭')


def _task_options(parser, name):
    if name == 'run':
        parser.add_argument('task', type=Path)
    else:
        parser.add_argument('url')
        parser.add_argument('--task', type=Path, required=True, help='可信本地仓库与检查模板')
    parser.add_argument('--no-workspace', action='store_true')
    parser.add_argument('--no-review', action='store_true')
    parser.add_argument('--max-rework', type=int)


def build_parser():
    parser = argparse.ArgumentParser(description='RepoPilot: Issue -> worktree -> review -> approval -> draft PR')
    parser.add_argument('--version', action='version', version='RepoPilot ' + version('repopilot'))
    sub = parser.add_subparsers(dest='command', required=True)
    for name in sorted(COMMANDS):
        command = sub.add_parser(name)
        command.add_argument('--output', type=Path, default=default_runtime_root(),
                             help='持久化运行目录（默认：%(default)s）')
        if name == 'memory':
            from repopilot.memory.cli import configure
            configure(command)
            continue
        if name == 'human':
            from repopilot.collaboration.cli import configure
            configure(command)
            continue
        if name == 'knowledge':
            from repopilot.knowledge.cli import configure
            configure(command)
            continue
        if name == 'watch':
            _watch_options(command)
            continue
        if name == 'jobs':
            continue
        if name in {'run', 'issue'}:
            _task_options(command, name)
        else:
            command.add_argument('run_id')
        if name in {'run', 'issue', 'resume', 'retry'}:
            _execution_options(command, name)
        if name == 'retry':
            command.add_argument('--reason', required=True)
        if name in {'approve', 'reject', 'recover'}:
            command.add_argument('--commit', required=True, help='核对过的完整提交 SHA')
            command.add_argument('--reason', required=True)
        if name == 'report':
            command.add_argument('--html', type=Path, help='报告输出路径，默认写到输出目录的 reports/')
    return parser


def _run_settings(args):
    settings = load_settings()
    overrides = load_profile(args.profile) if getattr(args, 'profile', None) else {}
    overrides.update({key: getattr(args, key) for key in
                      ('max_context_bytes', 'max_tool_calls', 'max_run_cost_cny', 'max_rework', 'memory_enabled')
                      if getattr(args, key, None) is not None})
    return type(settings).model_validate({**settings.model_dump(), **overrides}), overrides


def _run_task(args):
    settings, _ = _run_settings(args)
    task, issue, base_commit = load_task(args.task), None, None
    if args.command == 'issue':
        if args.no_workspace or args.no_review:
            raise StateError('issue_requires_workspace_and_review')
        with closing(open_forge(args.url)) as forge:
            task, issue = forge.intake(args.url, task)
            base_commit = forge.prepare_base(task.repository, issue['repository'])
    return workflow.start(task, settings, args.output, workspace=not args.no_workspace,
                          review=not args.no_review, require_approval=True, stop_after=args.stop_after,
                          issue=issue, base_commit=base_commit)


def _retry(args):
    settings, overrides = _run_settings(args)
    state = JobStore(args.output).get(args.run_id)
    base_commit = None
    if state.get('issue'):
        with closing(open_forge(state['issue']['url'])) as forge:
            current = forge.issue(state['issue']['url'])
            if workflow.canonical_hash(current) != workflow.canonical_hash(state['issue']):
                raise StateError('issue_changed_since_intake')
            base_commit = forge.prepare_base(Path(state['task']['repository']), state['issue']['repository'])
    return workflow.retry(args.run_id, settings, args.output, reason=args.reason,
                          overrides=overrides, stop_after=args.stop_after, base_commit=base_commit)


def _decision(args):
    if not args.reason.strip():
        raise StateError('reason_required')
    if args.command == 'recover':
        return workflow.recover(args.run_id, args.output, commit=args.commit, reason=args.reason)
    return workflow.decide(args.run_id, args.output, commit=args.commit,
                           approved=args.command == 'approve', reason=args.reason)


def _watch(args):
    from repopilot.delivery.watcher import IssueQueue, load_watch_config, watch
    queue = IssueQueue(args.output)
    if args.acknowledge:
        queue.acknowledge(args.acknowledge, args.reason or '')
        return {'status': 'ok', 'queue': queue.list()}
    if args.stop:
        (queue.root / 'watcher.stop').write_text('stop', encoding='ascii')
        return {'status': 'ok', 'stop_requested': True}
    if args.status:
        return {'status': 'ok', 'watcher': queue.health(), 'queue': queue.list()}
    if not args.config:
        raise StateError('watch_config_required')
    return watch(load_watch_config(args.config), args.output, once=args.once)


def execute_command(args):
    """分派应用操作；凭证只在需要模型或仓库平台的命令里加载。"""
    if args.command == 'human':
        from repopilot.collaboration.cli import execute
        return execute(args)
    if args.command == 'knowledge':
        from repopilot.knowledge.cli import execute
        return execute(args)
    if args.command == 'memory':
        from repopilot.memory.cli import execute
        return execute(args)
    if args.command in {'run', 'issue'}:
        return _run_task(args)
    if args.command == 'retry':
        return _retry(args)
    if args.command == 'resume':
        return workflow.resume(args.run_id, load_settings(), args.output, stop_after=args.stop_after)
    if args.command == 'watch':
        return _watch(args)
    if args.command in {'approve', 'reject', 'recover'}:
        return _decision(args)
    if args.command == 'jobs':
        return {'status': 'ok', 'jobs': JobStore(args.output).list()}
    if args.command == 'status':
        state = JobStore(args.output).get(args.run_id)
        return {key: state.get(key) for key in STATUS_FIELDS}
    if args.command == 'report':
        from repopilot.presentation import report as report_module
        state = JobStore(args.output).get(args.run_id)
        destination = args.html or (args.output / 'reports' / f'{args.run_id}.html')
        path = report_module.write(state, args.output / 'runs' / args.run_id, PROJECT_ROOT, destination)
        return {'status': 'ok', 'run_id': args.run_id, 'html': str(path.resolve()),
                'bytes': path.stat().st_size}
    if args.command == 'publish':
        # 从待发布运行的 Issue 链接决定平台，避免让调用方再声明一次。
        state = JobStore(args.output).get(args.run_id)
        issue = state.get('issue') or {}
        subject = issue.get('url') or state['task']['repository']
        with closing(open_forge(subject)) as forge:
            return forge_publish(args.run_id, args.output, forge)
    raise StateError('unknown_command')


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and not argv[0].startswith('-'):
        argv.insert(0, 'run')
    args = build_parser().parse_args(argv)
    try:
        result = execute_command(args)
    except Exception as exc:
        result = {'status': 'error', 'error_type': type(exc).__name__}
        if isinstance(exc, (StateError, ForgeError)):
            result['reason'] = str(exc)
        print(json.dumps(result, ensure_ascii=True))
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result['status'] in SUCCESS_STATUSES else 1


if __name__ == '__main__':
    raise SystemExit(main())
