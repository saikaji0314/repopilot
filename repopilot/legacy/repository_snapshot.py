"""收集仓库源码快照，生成 LLM 可读的文本表示。"""

from pathlib import Path

# 可发送给 LLM 的文件后缀（受控白名单）
_ALLOWED_SUFFIXES = {
    ".py",
    ".sql",
    ".html",
    ".css",
    ".js",
    ".txt",
    ".rst",
    ".md",
    ".toml",
    ".cfg",
    ".ini",
    ".yaml",
    ".yml",
    ".json",
}

# 需要排除的目录
_EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "instance",
    "htmlcov",
    ".mypy_cache",
    ".tox",
    "node_modules",
}

# 需要排除的文件后缀（二进制 / 缓存 / 数据库）
_EXCLUDE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".sqlite",
    ".db",
}

# 需要排除的特定文件名（密钥等敏感文件）
_EXCLUDE_FILE_NAMES = {
    ".env",
    ".env.example",
    "mutation.patch",
    "oracle.yaml",
}

_MAX_FILE_BYTES = 1_000_000


def collect_source_files(repo_path: Path):
    """
    从仓库根目录递归收集所有允许的文本文件，拼接成带路径标签的文本快照。

    收集规则：
      - 后缀必须在 _ALLOWED_SUFFIXES 白名单中
      - 目录不在 _EXCLUDE_DIRS 中
      - 后缀不在 _EXCLUDE_SUFFIXES 中
      - 文件名不在 _EXCLUDE_FILE_NAMES 中

    :param repo_path: 仓库根目录
    :return: 拼接后的源码快照字符串
    """
    repo_path = Path(repo_path)
    parts: list[str] = []

    for file_path in sorted(repo_path.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.name in _EXCLUDE_FILE_NAMES:
            continue
        if file_path.suffix.lower() in _EXCLUDE_SUFFIXES:
            continue
        if file_path.suffix.lower() not in _ALLOWED_SUFFIXES:
            continue
        if any(d in _EXCLUDE_DIRS for d in file_path.parts):
            continue
        if file_path.stat().st_size > _MAX_FILE_BYTES:
            continue

        relative = file_path.relative_to(repo_path)
        parts.append("=" * 60)
        parts.append(f"# 文件: {relative}")
        parts.append("=" * 60)
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        parts.append(content)
        parts.append("")

    return "\n".join(parts)
