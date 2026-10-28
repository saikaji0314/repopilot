"""审核证据查看、争议人工判断与返工额度延长命令；不需要模型或平台凭证。"""

import argparse
from pathlib import Path

from repopilot.review import gate


def configure(parser):
    commands = parser.add_subparsers(dest="review_command", required=True)
    for name in ("show", "decide", "continue"):
        command = commands.add_parser(name)
        command.add_argument("run_id")
        # 支持 --output 放在 review 前层选项或具体子命令之后。
        command.add_argument("--output", type=Path, default=argparse.SUPPRESS)
        if name == "decide":
            command.add_argument("--dispute", required=True, help="争议ID，例如 d-1")
            command.add_argument("--decision", required=True, choices=["confirmed", "refuted"],
                                 help="confirmed=该发现成立；refuted=排除该发现")
            command.add_argument("--reason", required=True, help="核对证据后的判断理由")
        if name == "continue":
            command.add_argument("--extra", type=int, default=1,
                                 help="在现有上限上明确增加的修改次数")
            command.add_argument("--reason", required=True)


def execute(args):
    if args.review_command == "show":
        return gate.show(args.run_id, args.output)
    if args.review_command == "decide":
        return gate.decide(args.run_id, args.output, dispute_id=args.dispute,
                           decision=args.decision, reason=args.reason)
    return gate.extend(args.run_id, args.output, extra=args.extra, reason=args.reason)
