"""按显式任务与命名验证命令在本机执行一次调查。"""

import argparse
import json
from pathlib import Path

from .agent import investigate
from .config import PROJECT_ROOT, load_settings, load_task


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="RepoPilot repository investigation (no editing tools)")
    parser.add_argument("task", type=Path, help="version 2.0 task YAML")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / ".local" / "runtime")
    args = parser.parse_args(argv)
    try:
        task = load_task(args.task)
        settings = load_settings()
        summary = investigate(task, settings, args.output)
    except Exception as exc:
        print(json.dumps({"status": "configuration_error", "error_type": type(exc).__name__,
                          "hint": "检查任务 YAML、仓库路径及 DS_API_KEY / DS_BASE_URL / DS_MODEL 配置"},
                         ensure_ascii=False))
        return 2
    # 用 JSON 转义保证任意工具输出能在 Windows GBK／ASCII 控制台打印。
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0 if summary["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
