"""为每次运行准备独立的 Git 工作区，并采集工作区相对基线的变更证据。

本模块只负责工作区生命周期与差异事实，不判断改动是否正确。
工作区建在原仓库之外，因此不继承原仓库自身的忽略规则。
"""

from hashlib import sha256
import os
from pathlib import Path
import subprocess
import tempfile
from threading import Lock

MAX_DIFF_CHARS = 4_000
MAX_CHANGE_LINES = 400


class WorkspaceError(RuntimeError):
    """工作区无法准备或无法采集变更。"""


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """在指定仓库内执行 git；不继承模型密钥等环境变量。"""

    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP",
                                      "HOME", "USERPROFILE", "LANG", "LC_ALL", "GIT_CONFIG_NOSYSTEM"}}
    environment.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                            env=environment, shell=False, timeout=120)
    if check and result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise WorkspaceError(f"git {' '.join(args)} failed: {message[-1] if message else result.returncode}")
    return result


def head_commit(repo: Path) -> str:
    return run_git(repo, "rev-parse", "HEAD").stdout.decode("ascii", errors="replace").strip()


class Workspace:
    """一个基于 git worktree 的独立工作副本。"""

    def __init__(self, repository: Path, root: Path):
        self.source = repository.resolve(strict=True)
        self.root = root.resolve()
        self.path: Path | None = None
        self.base_commit = ""

    def prepare(self) -> "Workspace":
        if not (self.source / ".git").exists():
            raise WorkspaceError("repository is not a git work tree; a worktree workspace requires .git")
        self.base_commit = head_commit(self.source)
        self.root.mkdir(parents=True, exist_ok=True)
        if self.path is not None:
            return self
        run_git(self.source, "worktree", "prune")
        self.path = self._create()
        return self

    def _create(self) -> Path:
        candidate = Path(tempfile.mkdtemp(prefix="worktree-", dir=self.root))
        candidate.rmdir()
        result = run_git(self.source, "worktree", "add", "--detach", str(candidate), self.base_commit, check=False)
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
            raise WorkspaceError(f"worktree_add_failed: {message[-1] if message else result.returncode}")
        if not candidate.is_dir():
            raise WorkspaceError("worktree_missing_after_add")
        return candidate

    def repository(self) -> Path:
        if self.path is None:
            raise WorkspaceError("workspace_not_prepared")
        return self.path

    def release(self) -> None:
        """移除工作区登记，保留目录供事后核对。"""

        if self.path is None:
            return
        run_git(self.source, "worktree", "prune", check=False)

    def changes(self) -> dict:
        """采集相对基线提交的变更；只报告事实，不评价改动。"""

        worktree = self.repository()
        status = run_git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
        entries, files = status.split(b"\0"), []
        index, untracked = 0, []
        while index < len(entries):
            entry = entries[index]
            index += 1
            if not entry:
                continue
            code, name = entry[:2].decode("ascii", errors="replace"), entry[3:].decode("utf-8", errors="replace")
            if code == "??":
                untracked.append(name)
            files.append({"path": name, "status": code})
        counts = self._counts(untracked)
        additions = deletions = 0
        for item in files:
            item["additions"], item["deletions"], item["binary"] = counts.get(item["path"], (0, 0, False))
            additions += item["additions"]
            deletions += item["deletions"]
        files.sort(key=lambda item: item["path"])
        return {"base_commit": self.base_commit, "worktree": str(worktree),
                "files": files, "changed_files": len(files),
                "additions": additions, "deletions": deletions}

    def _counts(self, untracked: list[str]) -> dict:
        counts = {}
        worktree = self.repository()
        for line in run_git(worktree, "diff", "--numstat", "HEAD").stdout.decode("utf-8", errors="replace").splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                added, removed, name = parts
                if added == "-" or removed == "-":
                    counts[name] = (0, 0, True)
                else:
                    counts[name] = (int(added), int(removed), False)
        for name in untracked:
            try:
                data = (worktree / name).read_bytes()
            except OSError:
                counts[name] = (0, 0, False)
                continue
            if b"\0" in data:
                counts[name] = (0, 0, True)
            else:
                counts[name] = (len(data.decode("utf-8", errors="replace").splitlines()), 0, False)
        return counts

    def diff(self, limit: int = MAX_DIFF_CHARS) -> dict:
        """返回统一差异文本，按字符上限截断并显式标注。"""

        worktree = self.repository()
        text = run_git(worktree, "diff", "--no-color", "--unified=3", "HEAD").stdout.decode("utf-8", errors="replace")
        for name in [item["path"] for item in self.changes()["files"] if item["status"] == "??"]:
            try:
                data = (worktree / name).read_bytes()
            except OSError:
                text += f"\n--- /dev/null\n+++ b/{name}\n(unreadable; content not inlined)\n"
                continue
            if b"\0" in data:
                text += f"\n--- /dev/null\n+++ b/{name}\n(binary file added; content not inlined)\n"
            else:
                lines = data.decode("utf-8", errors="replace").splitlines()[:MAX_CHANGE_LINES]
                text += f"\n--- /dev/null\n+++ b/{name}\n" + "\n".join(f"+{line}" for line in lines) + "\n"
        return {"diff": text[:limit], "truncated": len(text) > limit, "characters": len(text)}


class EditBudget:
    """限制本次运行的文件编辑次数，并记录被编辑的路径。"""

    def __init__(self, limit: int):
        self.limit, self.count, self.touched = limit, 0, []
        self.lock = Lock()

    def take(self, path: str) -> None:
        with self.lock:
            if self.count >= self.limit:
                raise ValueError("edit_limit_reached")
            self.count += 1
            if path not in self.touched:
                self.touched.append(path)

    def snapshot(self) -> dict:
        return {"edits": self.count, "edit_limit": self.limit, "edited_files": sorted(self.touched)}


def source_snapshot(repo: Path) -> str:
    """记录原仓库状态摘要，用于证明运行期间原仓库未被改动。

    使用 git 自身状态（HEAD 与工作区条目），而不是逐个文件读取内容：
    对大仓库逐个哈希会带来秒级到分钟级开销，超出本项目的运行期限口径。
    """

    parts = [run_git(repo, "rev-parse", "HEAD").stdout,
             run_git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout]
    return sha256(b"".join(parts)).hexdigest()
