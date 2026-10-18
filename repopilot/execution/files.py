"""无模型依赖的路径与UTF-8文件访问规则，供宿主和容器共用。"""

import os
from fnmatch import fnmatchcase
from pathlib import Path, PureWindowsPath
import stat

DENIED = {
    ".git", ".local", ".venv", ".venv-flask-source", "venv", "node_modules",
    "__pycache__", ".pytest_cache", ".secrets", ".aws", ".ssh", "instance",
    "data", "output", "credentials", "cookies", "browser-profile",
}
MAX_FILE_BYTES = 256_000
MAX_OUTPUT_BYTES = 8_000
MAX_WRITE_CHARS = 100_000
MAX_SEARCH_FILES = 2_000
MAX_SEARCH_ENTRIES = 20_000


def sensitive(name: str) -> bool:
    name = name.casefold()
    return (name in DENIED or
            (name.startswith(".env") and name != ".env.example") or
            ".local." in name or
            Path(name).suffix in {".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3"})


def linked(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def walk_error(error):
    # os.walk 默认吞掉目录权限／I/O错误，不能据此声称搜索已覆盖整个范围。
    raise error


class Repository:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("not_a_directory")

    def resolve(self, relative: str, *, must_exist: bool = True) -> Path:
        path = self._guard(relative)
        candidate = self.root
        for part in path.parts:
            candidate = candidate / part
            if linked(candidate):
                raise ValueError("link_denied")
        if must_exist and not candidate.exists():
            raise ValueError("path_missing")
        if not candidate.resolve().is_relative_to(self.root):
            raise ValueError("path_denied")
        return candidate

    @staticmethod
    def _guard(relative: str) -> Path:
        """拒绝绝对路径、上级跳转、盘符与敏感名；读写共用同一套判断。"""

        path = Path(str(relative).replace("\\", "/"))
        if (not str(relative).strip() or path.is_absolute() or PureWindowsPath(relative).drive or
                PureWindowsPath(relative).root or
                any(part in {"", ".", ".."} or ":" in part or sensitive(part) for part in path.parts)):
            raise ValueError("path_denied")
        return path

    def files(self, path: str = "."):
        target = self.resolve(path)
        if target.is_file():
            yield target.relative_to(self.root).as_posix()
            return
        if not target.is_dir():
            raise ValueError("not_a_file_or_directory")
        for folder, dirs, files in os.walk(target, followlinks=False, onerror=walk_error):
            base = Path(folder)
            dirs[:] = sorted(d for d in dirs if not sensitive(d) and not linked(base / d))
            for name in sorted(files):
                if not sensitive(name) and not linked(base / name):
                    yield (base / name).relative_to(self.root).as_posix()

    def text(self, relative: str) -> str:
        path = self.resolve(relative)
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("file_too_large")
        if b"\0" in data:
            raise ValueError("binary_file")
        try:
            # 统一换行后再交给模型与替换：模型总是按 \n 给出片段，
            # 保留 CRLF 会让精确替换在 Windows 上无故失败。这是有意的取舍。
            return data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            raise ValueError("not_utf8") from None

    def read(self, path: str, start_line: int, end_line: int) -> dict:
        if not 1 <= start_line <= end_line or end_line - start_line >= 200:
            raise ValueError("invalid_line_range")
        lines = self.text(path).splitlines()
        content = "\n".join(f"{i + 1}: {lines[i]}" for i in range(start_line - 1, min(end_line, len(lines))))
        return {"path": path, "content": content[:8000], "truncated": len(content) > 8000}

    def _selected_files(self, path, file_glob, coverage):
        # 模式仅匹配文件名；目录由受路径边界校验的 path 参数选择。
        if (not file_glob or len(file_glob) > 120 or
                any(char in file_glob for char in ("/", "\\", ":", "\0"))):
            raise ValueError("invalid_file_glob")
        for name in self.files(path):
            if coverage["visited_files"] >= MAX_SEARCH_ENTRIES:
                coverage["truncation_reason"] = "entry_limit"
                return
            coverage["visited_files"] += 1
            if fnmatchcase(Path(name).name, file_glob):
                yield name

    @staticmethod
    def _coverage(path, file_glob):
        return {"path": Path(str(path).replace("\\", "/")).as_posix(), "file_glob": file_glob,
                "visited_files": 0, "truncation_reason": None}

    def listing(self, path: str = ".", file_glob: str = "*") -> dict:
        coverage = self._coverage(path, file_glob)
        files = []
        for name in self._selected_files(path, file_glob, coverage):
            if len(files) >= 200:
                coverage["truncation_reason"] = "result_limit"
                break
            files.append(name)
        return {"files": files, **coverage, "truncated": coverage["truncation_reason"] is not None}

    def search(self, query: str, path: str = ".", file_glob: str = "*") -> dict:
        if not query or len(query) > 200:
            raise ValueError("invalid_query")
        coverage = self._coverage(path, file_glob)
        matches, skipped, scanned = [], 0, 0
        for name in self._selected_files(path, file_glob, coverage):
            if scanned >= MAX_SEARCH_FILES:
                coverage["truncation_reason"] = "file_limit"
                break
            scanned += 1
            try:
                lines = self.text(name).splitlines()
            except (ValueError, OSError):
                skipped += 1
                continue
            for line, content in enumerate(lines, 1):
                if query in content:
                    matches.append({"path": name, "line": line, "content": content[:200]})
                    if len(matches) >= 20:
                        coverage["truncation_reason"] = "match_limit"
                        break
            if coverage["truncation_reason"]:
                break
        truncated = coverage["truncation_reason"] is not None
        return {"matches": matches, "skipped_files": skipped, "scanned_files": scanned, **coverage,
                "truncated": truncated, "complete": not truncated and skipped == 0}

    def write(self, path: str, content: str) -> dict:
        """在工作区内写入 UTF-8 文本；路径判断与读取共用同一套边界。"""

        if len(content) > MAX_WRITE_CHARS:
            raise ValueError("content_too_large")
        if "\0" in content:
            raise ValueError("binary_content")
        target = self.resolve(path, must_exist=False)
        if not target.parent.resolve().is_relative_to(self.root):
            raise ValueError("path_denied")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
        return {"path": path, "characters": len(content), "lines": len(content.splitlines())}

    def replace(self, path: str, old: str, new: str) -> dict:
        """精确替换一段文本；找不到或多处匹配都拒绝，不做模糊替换。"""

        if not old:
            raise ValueError("empty_old_string")
        if len(new) > MAX_WRITE_CHARS:
            raise ValueError("content_too_large")
        if "\0" in new:
            raise ValueError("binary_content")
        target = self.resolve(path)
        text = self.text(path)
        occurrences = text.count(old)
        if occurrences == 0:
            raise ValueError("old_string_not_found")
        if occurrences > 1:
            raise ValueError("old_string_not_unique")
        replaced = text.replace(old, new, 1)
        if len(replaced) > MAX_WRITE_CHARS:
            raise ValueError("content_too_large")
        target.write_text(replaced, encoding="utf-8", newline="")
        line = text[:text.index(old)].count("\n") + 1
        return {"path": path, "line": line, "removed_characters": len(old),
                "added_characters": len(new), "replaced": 1}
