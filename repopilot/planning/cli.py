"""计划查看与子Issue映射命令；映射才需要平台凭证。"""

import argparse
from contextlib import closing
from pathlib import Path

from repopilot.delivery.forge import open_forge
from repopilot.planning import mapping
from repopilot.storage.jobs import JobStore, StateError


def configure(parser):
    commands = parser.add_subparsers(dest="plan_command", required=True)
    for name in ("show", "map"):
        command = commands.add_parser(name)
        command.add_argument("run_id")
        # 支持 --output 放在 plan 前层选项或具体子命令之后。
        command.add_argument("--output", type=Path, default=argparse.SUPPRESS)


def execute(args):
    if args.plan_command == "show":
        return mapping.show(args.run_id, args.output)
    state = JobStore(args.output).get(args.run_id)
    issue = state.get("issue") or {}
    if not issue.get("url"):
        raise StateError("issue_required_for_subtask_mapping")
    with closing(open_forge(issue["url"])) as forge:
        return mapping.map_subtasks(args.run_id, args.output, forge)
