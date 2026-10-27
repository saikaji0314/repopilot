"""本地人工请求命令；评论投递与同步才会加载平台凭证。"""

import argparse
from pathlib import Path

from repopilot.orchestration import human


def configure(parser):
    commands = parser.add_subparsers(dest="human_command", required=True)
    for name in ("list", "answer", "post", "sync", "preflight"):
        command = commands.add_parser(name)
        command.add_argument("run_id")
        # 支持 --output 放在 human 前层选项或具体子命令之后。
        command.add_argument("--output", type=Path, default=argparse.SUPPRESS)
        if name == "answer":
            command.add_argument("request_id")
            command.add_argument("--version", required=True, help="请求中展示的完整版本摘要")
            command.add_argument("--answer", required=True, help="本机操作者确认的业务答复")


def execute(args):
    if args.human_command == "list":
        return human.list_requests(args.run_id, args.output)
    if args.human_command == "post":
        return human.post_issue(args.run_id, args.output)
    if args.human_command == "sync":
        return human.sync_issue(args.run_id, args.output)
    if args.human_command == "preflight":
        return human.preflight_issue(args.run_id, args.output)
    return human.answer(args.run_id, args.output, request_id=args.request_id,
                        version=args.version, text=args.answer)
