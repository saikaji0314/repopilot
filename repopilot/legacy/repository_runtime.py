"""可信任务的仓库选择和测试环境；不接受模型指定解释器。"""

import os
from pathlib import Path

from repopilot.legacy.config import PROJECT_ROOT


def resolve_repository(task: dict, default_repo: Path) -> tuple[Path, Path | None]:
    repository = task.get("repository", {})
    relative = repository.get("baseline_path")
    if relative is None:
        return default_repo, None  # 兼容旧调用和本地 Fake 编排测试。
    path = Path(relative)
    if path.is_absolute():
        raise ValueError("baseline_path 必须是项目内相对路径")
    resolved = (PROJECT_ROOT / path).resolve()
    root = (PROJECT_ROOT / "target-repos").resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError("baseline_path 必须位于 target-repos 子目录")
    if not resolved.is_dir():
        raise FileNotFoundError(f"任务仓库不存在: {resolved}")
    runtime = repository.get("runtime", "default")
    if runtime == "default":
        return resolved, None
    if runtime != "flask-source":
        raise ValueError(f"不支持的测试运行环境: {runtime}")
    python = PROJECT_ROOT / ".venv-flask-source" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    if not python.is_file():
        raise FileNotFoundError("请先按 docs/phase-2/LARGER_REPO.md 创建 Flask 测试环境")
    return resolved, python


def workspace_test_env(workspace: Path) -> dict[str, str]:
    """src 布局优先导入当前副本，避免测试误用 site-packages 中的干净包。"""
    env = os.environ.copy()
    source = (workspace / "src").resolve()
    if source.is_dir():
        if not source.is_relative_to(workspace.resolve()):
            raise ValueError("src 路径越出工作区")
        previous = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(source) + (os.pathsep + previous if previous else "")
    return env
