"""按显式任务与命名验证命令在本机执行一次调查或实现。"""

import argparse
import json
from pathlib import Path

from .agent import investigate
from .config import PROJECT_ROOT, load_settings, load_task


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="RepoPilot repository run (read-only or in a git worktree)")
    parser.add_argument("task", type=Path, help="version 2.0 task YAML")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / ".local" / "runtime")
    parser.add_argument("--no-workspace", action="store_true",
                        help="读取类工具直接指向原仓库，不建独立工作区，也不提供编辑工具")
    parser.add_argument("--no-review", action="store_true",
                        help="只做实现，不进入审核与返工阶段")
    parser.add_argument("--max-rework", type=int, default=None,
                        help="审核不通过后允许返工的轮数上限，默认取任务配置（3）")
    args = parser.parse_args(argv)
    try:
        task = load_task(args.task)
        settings = load_settings()
        if args.max_rework is not None:
            settings = settings.model_copy(update={"max_rework": args.max_rework})
        summary = investigate(task, settings, args.output,
                              workspace=not args.no_workspace, review=not args.no_review)
    except Exception as exc:
        print(json.dumps({"status": "configuration_error", "error_type": type(exc).__name__,
                          "hint": "检查任务 YAML、仓库路径、git 工作树及 DS_API_KEY / DS_BASE_URL / DS_MODEL 配置"},
                         ensure_ascii=False))
        return 2
    # 用 JSON 转义保证任意工具输出能在 Windows GBK／ASCII 控制台打印。
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0 if summary["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
