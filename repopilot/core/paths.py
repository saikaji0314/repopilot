"""区分源码运行与安装包运行；路径选择无目录创建或环境修改副作用。"""

import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def source_checkout():
    return ((PROJECT_ROOT / "pyproject.toml").is_file() and
            (PROJECT_ROOT / "repopilot/core/config.py").is_file())


def _override(name):
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser().resolve() if value else None


def default_env_file():
    return _override("REPOPILOT_ENV_FILE") or (
        (PROJECT_ROOT if source_checkout() else Path.cwd()) / ".env")


def default_runtime_root():
    explicit = _override("REPOPILOT_RUNTIME_DIR")
    if explicit:
        return explicit
    if source_checkout():
        return PROJECT_ROOT / ".local/runtime"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        return base / "RepoPilot/runtime"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/RepoPilot/runtime"
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    if not base.is_absolute():
        base = Path.home() / ".local/share"
    return base / "repopilot/runtime"
