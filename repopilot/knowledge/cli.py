"""本机登记资料、确认具体版本及检索原文，不读取任何API凭证。"""

from pathlib import Path

from .documents import KnowledgeFault, Material
from .retrieval import related, search
from .store import KnowledgeStore


def configure(parser):
    parser.add_argument("action", choices=["register", "sync", "list", "search", "read", "confirm", "supersede", "refresh"])
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--id")
    parser.add_argument("--path")
    parser.add_argument("--kind", choices=["project", "task", "decision", "evidence"], default="project")
    parser.add_argument("--title")
    parser.add_argument("--scope", default="repository")
    parser.add_argument("--required", action="store_true")
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--relate", action="append", default=[], help="关联类型:目标资料ID，可重复")
    parser.add_argument("--query", default="")
    parser.add_argument("--proposed", action="store_true")
    parser.add_argument("--version")
    parser.add_argument("--reason", default="")
    parser.add_argument("--replacement")
    parser.add_argument("--start-line", type=int, default=1)
    parser.add_argument("--end-line", type=int, default=120)
    parser.add_argument("--run-id")
    parser.add_argument("--request-id")
    parser.add_argument("--commit")


def execute(args):
    if args.action == "refresh":
        from repopilot.orchestration.knowledge import refresh
        return refresh(args.run_id, args.output, request_id=args.request_id, version=args.version,
                       commit=args.commit, reason=args.reason)
    if args.repository is None:
        raise KnowledgeFault("knowledge_repository_required")
    store, repository = KnowledgeStore(args.output), args.repository.resolve(strict=True)
    if args.action == "register":
        if not args.id or not args.path or not args.title:
            raise KnowledgeFault("knowledge_id_path_title_required")
        relations = {}
        for item in args.relate:
            relation, separator, target = item.partition(":")
            if not separator or not relation.strip() or not target.strip():
                raise KnowledgeFault("knowledge_relation_invalid")
            relations.setdefault(relation, []).append(target)
        result = store.register(repository, args.path, Material(id=args.id, kind=args.kind, title=args.title,
            scope=args.scope, required=args.required, sources=args.source, relations=relations))
    elif args.action == "sync":
        result = store.sync(repository)
    elif args.action == "list":
        store.sync(repository)
        result = [store.inspect(repository, r["id"]) for r in store.records(repository)]
    elif args.action == "search":
        result = search(store, repository, args.query, scope=args.scope, proposed=args.proposed)
    else:
        if not args.id:
            raise KnowledgeFault("knowledge_id_required")
        if args.action == "read":
            store.sync(repository)
            result = store.read(repository, args.id, version=args.version,
                                start_line=args.start_line, end_line=args.end_line)
            result["relation_status"] = related(store, repository, args.id, result["relations"])
        elif args.action == "confirm":
            result = store.confirm(repository, args.id, version=args.version, reason=args.reason)
        else:
            result = store.supersede(repository, args.id, replacement=args.replacement, reason=args.reason)
    return {"status": "ok", "knowledge": result}
