"""任务运行、检查点恢复、人工审批及 GitHub 草稿 PR 的命令入口。"""
import argparse
import json
from pathlib import Path
import sys
from .config import PROJECT_ROOT, load_profile, load_settings, load_task
from . import workflow
from .github import GitHub, GitHubError, load_token, publish
from .store import JobStore, StateError

COMMANDS = {'run', 'issue', 'resume', 'retry', 'status', 'jobs', 'approve', 'reject', 'recover', 'publish', 'watch'}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and not argv[0].startswith('-'):
        argv.insert(0, 'run')
    parser = argparse.ArgumentParser(description='RepoPilot: Issue -> worktree -> review -> approval -> draft PR')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in sorted(COMMANDS):
        command = sub.add_parser(name)
        command.add_argument('--output', type=Path, default=PROJECT_ROOT / '.local' / 'runtime')
        if name == 'watch':
            command.add_argument('--config', type=Path)
            command.add_argument('--once', action='store_true', help='只轮询一次并处理本轮队列')
            command.add_argument('--status', action='store_true', help='查看持久化 Issue 队列，不调用模型或 GitHub')
            command.add_argument('--stop', action='store_true', help='请求监听器停止接单，等待当前任务落盘后退出')
            command.add_argument('--acknowledge', help='核查残留 worker 后确认一个 interrupted Issue 队列键')
            command.add_argument('--reason')
            continue
        if name == 'jobs':
            continue
        if name in {'run', 'issue'}:
            if name == 'run':
                command.add_argument('task', type=Path)
            else:
                command.add_argument('url')
                command.add_argument('--task', type=Path, required=True, help='可信本地仓库与检查模板')
            command.add_argument('--no-workspace', action='store_true')
            command.add_argument('--no-review', action='store_true')
            command.add_argument('--max-rework', type=int)
        else:
            command.add_argument('run_id')
        if name in {'run', 'issue', 'resume', 'retry'}:
            command.add_argument('--stop-after', choices=['prepare', 'implement', 'anchor', 'verify', 'review', 'rework'])
        if name in {'run', 'issue', 'retry'}:
            command.add_argument('--profile', type=Path, help='执行限额与思考模式 YAML，不含凭证')
            command.add_argument('--max-context-bytes', type=int)
            command.add_argument('--max-tool-calls', type=int)
            command.add_argument('--max-run-cost-cny', type=float)
        if name == 'retry':
            command.add_argument('--reason', required=True)
        if name in {'approve', 'reject', 'recover'}:
            command.add_argument('--commit', required=True, help='核对过的完整提交 SHA')
            command.add_argument('--reason', required=True)
    args = parser.parse_args(argv)
    github = None
    try:
        if args.command == 'watch':
            from .watcher import IssueQueue, load_watch_config, watch
            if args.acknowledge:
                queue = IssueQueue(args.output)
                queue.acknowledge(args.acknowledge, args.reason or '')
                result = {'status': 'ok', 'queue': queue.list()}
            elif args.stop:
                queue = IssueQueue(args.output)
                (queue.root / 'watcher.stop').write_text('stop', encoding='ascii')
                result = {'status': 'ok', 'stop_requested': True}
            elif args.status:
                queue = IssueQueue(args.output)
                result = {'status': 'ok', 'watcher': queue.health(), 'queue': queue.list()}
            elif args.config:
                result = watch(load_watch_config(args.config), args.output, once=args.once)
            else:
                raise StateError('watch_config_required')
        elif args.command == 'jobs':
            result = {'status': 'ok', 'jobs': JobStore(args.output).list()}
        elif args.command == 'status':
            state = JobStore(args.output).get(args.run_id)
            result = {k: state.get(k) for k in ('run_id', 'status', 'phase', 'in_flight', 'stop_reason',
                'reviewed_commit', 'report', 'review_report', 'verified', 'issue', 'approval', 'pull_request')}
        elif args.command in {'approve', 'reject', 'recover'}:
            if not args.reason.strip():
                raise StateError('reason_required')
            if args.command == 'recover':
                result = workflow.recover(args.run_id, args.output, commit=args.commit, reason=args.reason)
            else:
                result = workflow.decide(args.run_id, args.output, commit=args.commit,
                    approved=args.command == 'approve', reason=args.reason)
        elif args.command == 'publish':
            github = GitHub(load_token())
            result = publish(args.run_id, args.output, github)
        else:
            settings = load_settings()
            overrides = load_profile(args.profile) if getattr(args, 'profile', None) else {}
            overrides.update({k: getattr(args, k) for k in ('max_context_bytes', 'max_tool_calls', 'max_run_cost_cny', 'max_rework')
                              if getattr(args, k, None) is not None})
            settings = type(settings).model_validate({**settings.model_dump(), **overrides})
            if args.command == 'resume':
                result = workflow.resume(args.run_id, settings, args.output, stop_after=args.stop_after)
            elif args.command == 'retry':
                state = JobStore(args.output).get(args.run_id)
                base_commit = None
                if state.get('issue'):
                    github = GitHub(load_token())
                    if workflow.canonical_hash(github.issue(state['issue']['url'])) != workflow.canonical_hash(state['issue']):
                        raise StateError('issue_changed_since_intake')
                    base_commit = github.prepare_base(Path(state['task']['repository']), state['issue']['repository'])
                result = workflow.retry(args.run_id, settings, args.output, reason=args.reason,
                    overrides=overrides, stop_after=args.stop_after, base_commit=base_commit)
            else:
                task, issue, base_commit = load_task(args.task), None, None
                if args.command == 'issue':
                    if args.no_workspace or args.no_review:
                        raise StateError('issue_requires_workspace_and_review')
                    github = GitHub(load_token())
                    task, issue = github.intake(args.url, task)
                    base_commit = github.prepare_base(task.repository, issue['repository'])
                result = workflow.start(task, settings, args.output, workspace=not args.no_workspace,
                    review=not args.no_review, require_approval=True, stop_after=args.stop_after,
                    issue=issue, base_commit=base_commit)
    except Exception as exc:
        result = {'status': 'error', 'error_type': type(exc).__name__}
        if isinstance(exc, (StateError, GitHubError)):
            result['reason'] = str(exc)
        print(json.dumps(result, ensure_ascii=True))
        return 2
    finally:
        if github:
            github.close()
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result['status'] in {'ok', 'completed', 'paused', 'awaiting_approval', 'approved', 'published', 'rejected'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
