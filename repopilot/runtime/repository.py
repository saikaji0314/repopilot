"""对显式受信本地仓库做有界的 UTF-8 访问与命名检查。"""

import itertools
import os
from pathlib import Path, PureWindowsPath
import signal
import stat
import subprocess
import sys
import tempfile
from threading import Lock
import time

from langchain_core.tools import tool

from .config import Task

DENIED = {
    ".git", ".local", ".venv", ".venv-flask-source", "venv", "node_modules",
    "__pycache__", ".pytest_cache", ".secrets", ".aws", ".ssh", "instance",
    "data", "output", "credentials", "cookies", "browser-profile",
}
MAX_FILE_BYTES = 256_000
MAX_OUTPUT_BYTES = 8_000
MAX_WRITE_CHARS = 100_000


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
                any(part in {"", ".", ".."} or ":" in part or sensitive(part) for part in path.parts)):
            raise ValueError("path_denied")
        return path

    def files(self):
        for folder, dirs, files in os.walk(self.root, followlinks=False):
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

    def search(self, query: str) -> dict:
        if not query or len(query) > 200:
            raise ValueError("invalid_query")
        matches, skipped = [], 0
        for index, path in enumerate(self.files()):
            if index >= 2000:
                return {"matches": matches, "skipped_files": skipped, "truncated": True}
            try:
                lines = self.text(path).splitlines()
            except (ValueError, OSError):
                skipped += 1
                continue
            for line, content in enumerate(lines, 1):
                if query in content:
                    matches.append({"path": path, "line": line, "content": content[:200]})
                    if len(matches) >= 20:
                        return {"matches": matches, "skipped_files": skipped, "truncated": True}
        return {"matches": matches, "skipped_files": skipped, "truncated": False}

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


def kill_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        finally:
            if process.poll() is None:
                process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def run_check(task: Task, name: str, timeout: float, root: Path | None = None) -> dict:
    """执行登记检查；root 用于把工作目录边界套在独立工作区上。"""

    if name not in task.checks:
        raise ValueError("unknown_check")
    check = task.checks[name]
    cwd = Repository(root if root is not None else task.repository).resolve(check.cwd)
    argv = [sys.executable if arg == "{python}" else arg for arg in check.argv]
    # 不继承凭证、Python 路径以及浏览器／账号相关环境变量。
    keep = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "TEMP", "TMP",
            "HOME", "USERPROFILE", "LANG", "LC_ALL"}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    env.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    started, timed_out = time.monotonic(), False
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=output,
            stderr=subprocess.STDOUT, shell=False, start_new_session=os.name != "nt",
        )
        try:
            process.wait(timeout=min(timeout, check.timeout_seconds))
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_tree(process)
        except BaseException:
            kill_tree(process)
            raise
        length = output.seek(0, os.SEEK_END)
        output.seek(0)
        if length > MAX_OUTPUT_BYTES:
            marker = b"\n...[output truncated; head and tail retained]...\n"
            head_size = (MAX_OUTPUT_BYTES - len(marker)) // 2
            tail_size = MAX_OUTPUT_BYTES - len(marker) - head_size
            data = output.read(head_size)
            output.seek(-tail_size, os.SEEK_END)
            data += marker + output.read(tail_size)
        else:
            data = output.read()
    return {
        "name": name, "exit_code": process.returncode, "timed_out": timed_out,
        "output": data[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"),
        "truncated": length > MAX_OUTPUT_BYTES,
        "duration_seconds": round(time.monotonic() - started, 3),
    }


def build_tools(task: Task, remaining_seconds, workspace=None, edits=None):
    """装配读写工具；workspace 为空时只提供读取类工具。"""

    repo = Repository(workspace if workspace is not None else task.repository)
    executed, execution_lock = {}, Lock()

    def observe(action):
        try:
            return {"ok": True, **action()}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except OSError:
            return {"ok": False, "error": "repository_io_error"}

    @tool
    def list_files() -> dict:
        """列出仓库前 200 个可访问文件；敏感文件、链接和运行数据被过滤。"""
        def action():
            files = list(itertools.islice(repo.files(), 201))
            return {"files": files[:200], "truncated": len(files) > 200}
        return observe(action)

    @tool
    def read_file(path: str, start_line: int = 1, end_line: int = 120) -> dict:
        """读取仓库相对路径的 UTF-8 文本，行号从 1 开始，每次最多 200 行。"""
        return observe(lambda: repo.read(path, start_line, end_line))

    @tool
    def search_text(query: str) -> dict:
        """按区分大小写的字面子串搜索普通文本，最多返回 20 处匹配。"""
        return observe(lambda: repo.search(query))

    @tool
    def run_registered_check(name: str) -> dict:
        """执行任务登记的命名检查，同一编辑版本每项最多一次，编辑后可重跑；不能提供额外参数。"""
        def action():
            if name not in task.checks:
                raise ValueError("unknown_check")
            with execution_lock:
                revision = 0 if edits is None else edits.count
                if executed.get(name) == revision:
                    raise ValueError("check_already_ran")
                executed[name] = revision
            return run_check(task, name, remaining_seconds(), root=repo.root)
        return observe(action)

    tools = [list_files, read_file, search_text, run_registered_check]
    if workspace is None:
        return tools

    def editing(action, path):
        if edits is None:
            return observe(action)
        try:
            edits.take(path)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return observe(action)

    @tool
    def write_file(path: str, content: str) -> dict:
        """在工作区写入或覆盖 UTF-8 文本文件；不能写入敏感路径，单文件上限 100,000 字符。"""
        return editing(lambda: repo.write(path, content), path)

    @tool
    def replace_in_file(path: str, old: str, new: str) -> dict:
        """把工作区文件中的一段文本精确替换为新文本；old 必须唯一出现，否则拒绝。"""
        return editing(lambda: repo.replace(path, old, new), path)

    return tools + [write_file, replace_in_file]
