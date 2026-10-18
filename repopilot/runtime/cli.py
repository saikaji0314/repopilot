"""旧模块命令兼容入口，正式入口为 repopilot.cli。"""
from repopilot.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
