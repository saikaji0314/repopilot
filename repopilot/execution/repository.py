"""对显式受信本地仓库做有界的 UTF-8 访问与命名检查。"""

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import codecs
from threading import Lock
import time

from langchain_core.tools import tool

from repopilot.core.config import Task
from repopilot.execution.files import (Repository, DENIED, MAX_FILE_BYTES, MAX_OUTPUT_BYTES,
                          MAX_WRITE_CHARS, linked, sensitive)


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

    if task.execution.backend != 'local':
        raise ValueError('isolated_execution_required')
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
    from repopilot.execution.telemetry import emit_output
    with tempfile.TemporaryDirectory(prefix='repopilot-check-') as directory, \
            (Path(directory) / 'output').open('w+b') as output, \
            (Path(directory) / 'output').open('rb') as reader:
        process = subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=output,
            stderr=subprocess.STDOUT, shell=False, start_new_session=os.name != "nt",
        )
        try:
            deadline = started + min(timeout, check.timeout_seconds)
            position, pending = 0, ''
            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            while True:
                # Independent handles preserve the child's file offset on Windows and POSIX.
                data = reader.read(max(0, MAX_OUTPUT_BYTES - position))
                if data:
                    position += len(data)
                    pending += decoder.decode(data)
                    if '\n' in pending:
                        complete, pending = pending.rsplit('\n', 1)
                        emit_output(complete + '\n')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
                try:
                    process.wait(timeout=min(.15, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pass
            data = reader.read(max(0, MAX_OUTPUT_BYTES - position))
            emit_output(pending + decoder.decode(data, final=True))
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


def build_tools(task: Task, remaining_seconds, workspace=None, edits=None, *, repository=None, checker=None):
    """装配读写工具；workspace 为空时只提供读取类工具。"""

    if task.execution.backend != 'local' and (repository is None or checker is None):
        raise ValueError('isolated_execution_required')
    repo = repository if repository is not None else Repository(workspace if workspace is not None else task.repository)
    executed, execution_lock = {}, Lock()

    def observe(action):
        try:
            return {"ok": True, **action()}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except OSError:
            return {"ok": False, "error": "repository_io_error"}

    @tool
    def list_files(path: str = ".", file_glob: str = "*") -> dict:
        """递归列出 path 目录或单文件，最多200项。file_glob匹配文件名（如 *.ts），不含目录。

        默认全仓；路径始终相对仓库根。截断时缩小path或file_glob；敏感文件和链接不可访问。
        """
        return observe(lambda: repo.listing(path, file_glob))

    @tool
    def read_file(path: str, start_line: int = 1, end_line: int = 120) -> dict:
        """读取仓库相对路径的 UTF-8 文本，行号从 1 开始，每次最多 200 行。"""
        return observe(lambda: repo.read(path, start_line, end_line))

    @tool
    def search_text(query: str, path: str = ".", file_glob: str = "*") -> dict:
        """区分大小写的字面搜索，最多20处。path选择目录或单文件，file_glob匹配文件名（如 *.py）。

        默认全仓。优先用已知目录、文件类型缩小范围；结果路径相对仓库根。
        截断时依据truncation_reason缩小path、file_glob或query；complete=false时零匹配不代表不存在。
        """
        return observe(lambda: repo.search(query, path, file_glob))

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
            return (checker(name, remaining_seconds()) if checker is not None else
                    run_check(task, name, remaining_seconds(), root=repo.root))
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
