"""为单条评测任务创建、变异并清理隔离工作区。"""

from contextlib import contextmanager
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Iterator


_COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    ".venv",
    "venv",
    ".pytest_cache",
    "__pycache__",
    ".vscode",
    "instance",
    "*.pyc",
    "*.pyo",
    "*.sqlite",
    "*.db",
)


def _ensure_child(path: Path, parent: Path, *, label: str) -> Path:
    resolved_path = path.resolve()
    resolved_parent = parent.resolve()
    if resolved_path != resolved_parent and resolved_parent not in resolved_path.parents:
        raise ValueError(f"{label} 超出允许目录: {resolved_path}")
    return resolved_path


def apply_workspace_additions(
    copy_path: Path,
    task_dir: Path,
    additions: list[dict],
) -> None:
    """只复制 schema 中明确标为 agent_visible 的附加文件。"""
    for item in additions:
        if item.get("visibility") != "agent_visible":
            raise ValueError(f"不支持的 workspace addition visibility: {item!r}")

        source = _ensure_child(
            task_dir / str(item["source"]), task_dir, label="附加文件源路径"
        )
        destination = _ensure_child(
            copy_path / str(item["destination"]), copy_path, label="附加文件目标路径"
        )
        if not source.is_file():
            raise FileNotFoundError(f"附加文件不存在: {source}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def apply_mutation(copy_path: Path, patch_file: Path) -> None:
    patch_file = patch_file.resolve()
    if not patch_file.is_file():
        raise FileNotFoundError(f"缺陷补丁不存在: {patch_file}")

    for args, label in (
        (["git", "apply", "--check", str(patch_file)], "git apply --check"),
        (["git", "apply", str(patch_file)], "git apply"),
    ):
        result = subprocess.run(
            args,
            cwd=copy_path,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"{label} 失败: {detail}")


@contextmanager
def prepared_task_workspace(
    clean_repo: Path,
    task_dir: Path,
    task: dict,
) -> Iterator[Path]:
    """创建唯一临时目录；无论成功或异常，退出时都会清理。"""
    clean_repo = clean_repo.resolve()
    task_dir = task_dir.resolve()
    if not clean_repo.is_dir():
        raise FileNotFoundError(f"干净仓库不存在: {clean_repo}")

    safe_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(task["id"]))
    temp_root = Path(tempfile.mkdtemp(prefix=f"repopilot-{safe_id}-"))
    copy_path = temp_root / clean_repo.name

    try:
        shutil.copytree(clean_repo, copy_path, ignore=_COPY_IGNORE)
        apply_workspace_additions(
            copy_path,
            task_dir,
            task.get("workspace_additions", []),
        )
        apply_mutation(copy_path, task_dir / "mutation.patch")
        yield copy_path
    finally:
        if temp_root.exists():
            shutil.rmtree(temp_root)
