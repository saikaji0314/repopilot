# search_code
from pathlib import Path
import re
import os
from repopilot.agent.schemas import ToolError, ToolResult
from repopilot.tools.path_guard import WorkspacePathGuard
from repopilot.retrieval.search import search_index
from repopilot.retrieval.context import merge_overlapping_hits, build_context
from repopilot.retrieval.schema import Symbol, SymbolKind, HitReason, RepositoryIndex


IGNORED_DIR_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "instance",
}
ALLOWED_SUFFIXES = {
    ".py",
    ".html",
    ".sql",
    ".toml",
    ".md",
    ".yaml",
    ".yml",
    ".txt",
}


def search_code(workspace_path: Path, relative_path: Path, query: str, file_pattern: str) -> ToolResult:
    guard = WorkspacePathGuard(workspace_path)
    try:
        guard.check_path(relative_path)
    except ValueError as e:
        return ToolResult(
            ok=False,
            tool="search_code",
            data={},
            error=ToolError(code="path_outside_workspace", message=str(e)),
            truncated=False
        )
    # workspace_path / relative_path 存在校验
    if not (workspace_path / relative_path).exists():
        return ToolResult(
            ok=False,
            tool="search_code",
            data={},
            error=ToolError(code="path_not_found", message="relative_path not found"),
            truncated=False
        )    
    # workspace_path / relative_path 是路径校验
    if not (workspace_path / relative_path).is_dir():
        return ToolResult(
            ok=False,
            tool="search_code",
            data={},
            error=ToolError(code="not_a_directory", message=(
                "本次搜索未执行：search_code 的 path 必须是目录，不能是文件。"
                "请改为该文件的父目录，file_pattern 使用扩展名模式（如 *.py），"
                "然后重新调用 search_code；读取指定文件请使用 read_file。"
            )),
            truncated=False
        )
    # workspace_path / relative_path 是忽略目录校验
    if relative_path.name in IGNORED_DIR_NAMES:
        return ToolResult(
            ok=False,
            tool="search_code",
            data={},
            error=ToolError(code="ignored_directory", message="relative_path is ignored"),
            truncated=False
        )
    # 空query
    if not query:
        return ToolResult(
            ok=False,
            tool="search_code",
            data={},
            error=ToolError(code="empty_query", message="query is empty"),
            truncated=False
        )
    # query过长
    if len(query) > 200:
        return ToolResult(
            ok=False,
            tool="search_code",
            data={},
            error=ToolError(code="query_too_long", message="query too long"),
            truncated=False
        )
    # file_pattern只接受简单扩展名模式如*.py
    if not file_pattern.startswith("*") or file_pattern[1:] not in ALLOWED_SUFFIXES:
        return ToolResult(
            ok=False,
            tool="search_code",
            data={},
            error=ToolError(code="invalid_pattern", message=f"file_pattern must be of the form *.{', '.join(ALLOWED_SUFFIXES)}"),
            truncated=False
        )
    # 最多扫描 500 个文件，最多返回 50 个匹配
    max_files, max_matches = 500, 50
    matches = []
    search_file_cnt, match_cnt = 0, 0
    for current_dir, dirs, files in os.walk((workspace_path / relative_path).resolve()):
        dirs[:] = sorted(name for name in dirs if name not in IGNORED_DIR_NAMES)  # 防止进入忽略目录
        for filename in sorted(files):
            file = Path(current_dir) / filename
            if not file.match(file_pattern):
                continue
            try:
                content = file.resolve().read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

            # search in file
            for line_number, line in enumerate(content.splitlines(), start=1):
                if query in line:
                    matches.append(
                        {
                            "path": file.relative_to(workspace_path).as_posix(),
                            "line": line_number,
                            "text": line.rstrip(),
                        }
                    )
                    match_cnt += 1
                    if match_cnt >= max_matches:
                        return ToolResult(
                            ok=True,
                            tool="search_code",
                            data={"matches": matches},
                            error=None,
                            truncated=True
                        )

            search_file_cnt += 1
            if search_file_cnt >= max_files:
                return ToolResult(
                    ok=True,
                    tool="search_code",
                    data={"matches": matches},
                    error=None,
                    truncated=True
                )

    # 统一返回外壳
    return ToolResult(
        ok=True,
        tool="search_code",
        data={"matches": matches},
        error=None,
        truncated=False
    )


def search_symbols(workspace_path: Path, query: str, limit: int, index: RepositoryIndex) -> ToolResult:
    # 空query
    if not query:
        return ToolResult(
            ok=False,
            tool="search_code",
            data={},
            error=ToolError(code="empty_query", message="query is empty"),
            truncated=False
        )
    # query过长
    if len(query) > 200:
        return ToolResult(
            ok=False,
            tool="search_code",
            data={},
            error=ToolError(code="query_too_long", message="query too long"),
            truncated=False
        )
    hits = search_index(index, query, limit=limit)
    merged_hits = merge_overlapping_hits(hits)
    # 较长函数需要更多上下文；仍限制单次片段，后续可用 read_file 精读。
    context, truncated = build_context(workspace_path, merged_hits, max_chars=4000)
    return ToolResult(
        ok=True,
        tool="search_symbols",
        data={"context": context},
        error=None,
        truncated=truncated
    )
