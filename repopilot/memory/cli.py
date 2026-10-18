"""可信本机操作者管理仓库事实；不加载模型或GitHub凭证。"""

from pathlib import Path

from repopilot.memory.store import MemoryStore
from repopilot.memory.facts import MemoryFault


def configure(parser):
    parser.add_argument('action', choices=['remember', 'list', 'search', 'confirm', 'revoke', 'forget'])
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--path')
    parser.add_argument('--start-line', type=int, default=1)
    parser.add_argument('--end-line', type=int, default=8)
    parser.add_argument('--depends-on', action='append', default=[])
    parser.add_argument('--query', default='')
    parser.add_argument('--id')
    parser.add_argument('--reason', default='')


def execute(args):
    store, repository = MemoryStore(args.output), args.repository.resolve(strict=True)
    if args.action == 'remember':
        if not args.path:
            raise MemoryFault('memory_path_required')
        result = store.remember(repository, args.path, args.start_line, args.end_line, dependencies=args.depends_on)
    elif args.action == 'list':
        result = store.inspect(repository)
    elif args.action == 'search':
        result = store.search(repository, args.query)
    else:
        if not args.id:
            raise MemoryFault('memory_id_required')
        result = store.decide(repository, args.id, args.action, args.reason)
    return {'status': 'ok', 'memory': result}
