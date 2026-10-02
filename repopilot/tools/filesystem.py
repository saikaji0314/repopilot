# list_files、read_file
from pathlib import Path
from repopilot.tools.path_guard import WorkspacePathGuard
from repopilot.agent.schemas import ToolError, ToolResult


IGNORED_DIR_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "instance",
}
IGNORE_FILE_SUFFIXES = {
    ".db",
    ".sqlite",
    ".log",
}
ALLOWED_TEXT_SUFFIXES = {
    ".py",
    ".html",
    ".sql",
    ".toml",
    ".md",
    ".yaml",
    ".yml",
    ".txt",
}


def _collect_entries(path: Path, max_depth: int, limit: int = 200) -> tuple[list[Path], bool]:
    entries = []
    next_level_dirs = [path]
    for _ in range(max_depth):
        current_level_dirs = next_level_dirs
        next_level_dirs = []
        for dir in current_level_dirs:
            if dir != path:
                entries.append({"path": dir, "type": "directory"})
            for entry in dir.iterdir():
                if entry.is_dir() and entry.name not in IGNORED_DIR_NAMES:
                    next_level_dirs.append(entry)
                elif entry.is_file() and entry.suffix not in IGNORE_FILE_SUFFIXES:
                    entries.append({"path": entry, "type": "file"})
            if len(entries) >= limit:
                return entries, True
    return entries, False


def list_files(workspace_path: Path, relative_path: Path, max_depth: int) -> ToolResult:
    guard = WorkspacePathGuard(workspace_path)
    try:
        guard.check_path(relative_path)
    except ValueError as e:
        return ToolResult(
            ok=False,
            tool="list_files",
            data={},
            error=ToolError(code="path_outside_workspace", message=str(e)),
            truncated=False
        )
    # 校验max_depth
    if not 1 <= max_depth <= 4:
        return ToolResult(
            ok=False,
            tool="list_files",
            data={},
            error=ToolError(code="invalid_depth", message="max_depth must be between 1 and 4"),
            truncated=False
        )
    # 确认目标是目录
    if not Path.resolve(workspace_path / relative_path).is_dir():
        return ToolResult(
            ok=False,
            tool="list_files",
            data={},
            error=ToolError(code="not_a_directory", message="target must be a directory"),
            truncated=False
        )
    # 收集文件
    entries, truncated = _collect_entries(Path.resolve(workspace_path / relative_path), max_depth)
    entries = [{"path": entry["path"].relative_to(Path.resolve(workspace_path)).as_posix(), "type": entry["type"]} for entry in entries]
    # 统一返回外壳
    return ToolResult(
        ok=True,
        tool="list_files",
        data={"entries": entries},
        error=None,
        truncated=truncated
    )


def read_file(workspace_path: Path, relative_path: Path, start: int, end: int) -> ToolResult:
    guard = WorkspacePathGuard(workspace_path)
    try:
        guard.check_path(relative_path)
    except ValueError as e:
        return ToolResult(
            ok=False,
            tool="read_file",
            data={},
            error=ToolError(code="path_outside_workspace", message=str(e)),
            truncated=False
        )
    # 校验start和end
    if not 1 <= start <= end:
        return ToolResult(
            ok=False,
            tool="read_file",
            data={},
            error=ToolError(code="invalid_line_range", message="start and end must be between 1 and end"),
            truncated=False
        )
    # 要求目标是文件
    if not Path.resolve(workspace_path / relative_path).is_file():
        return ToolResult(
            ok=False,
            tool="read_file",
            data={},
            error=ToolError(code="not_a_file", message="file type not supported"),
            truncated=False
        )
    # 只接受允许的文本扩展名
    if relative_path.suffix.lower() not in ALLOWED_TEXT_SUFFIXES:
        return ToolResult(
            ok=False,
            tool="read_file",
            data={},
            error=ToolError(code="unsupported_file_type", message="file type not supported"),
            truncated=False
        )
    # 读取
    try:
        content = Path.resolve(workspace_path / relative_path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ToolResult(
            ok=False,
            tool="read_file",
            data={},
            error=ToolError(code="unsupported_encoding", message="file encoding error: utf-8"),
            truncated=False
        )
    except FileNotFoundError:
        return ToolResult(
            ok=False,
            tool="read_file",
            data={},
            error=ToolError(code="file_not_found", message="file not found"),
            truncated=False
        )
    
    row_limit, char_limit = 200, 20000  # 限制返回内容长度最多返还行、字符
    truncated = False
    if end - start + 1 > row_limit:
        truncated = True
    lines = content.splitlines()[start-1:end][:row_limit]

    char_count = 0
    for i in range(len(lines)):
        lines[i] = f"{start+i}: {lines[i]}"  # 输出带 1-based 行号
        char_count += len(lines[i]) + 1
        if char_count > char_limit:
            truncated = True
            lines = lines[:i]
            break
    
    content = "\n".join(lines)
    return ToolResult(
        ok=True,
        tool="read_file",
        data={"content": content},
        error=None,
        truncated=truncated
    )
