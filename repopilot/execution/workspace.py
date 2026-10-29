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


def run_git(repo: Path, *args: str, check: bool = True, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """在指定仓库内执行 git；不继承模型密钥等环境变量。"""

    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP",
                                      "HOME", "USERPROFILE", "LANG", "LC_ALL", "GIT_CONFIG_NOSYSTEM"}}
    environment.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
    environment.update(extra_env or {})
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

    def __init__(self, repository: Path, root: Path, *, path: Path | None = None, base_commit: str = ""):
        self.source = repository.resolve(strict=True)
        self.root = root.resolve()
        self.path: Path | None = path
        self.base_commit = base_commit

    def prepare(self) -> "Workspace":
        if self.root.is_relative_to(self.source):
            raise WorkspaceError("workspace_root_must_be_outside_source")
        if not (self.source / ".git").exists():
            raise WorkspaceError("repository is not a git work tree; a worktree workspace requires .git")
        if not self.base_commit:
            self.base_commit = head_commit(self.source)
        self.root.mkdir(parents=True, exist_ok=True)
        if self.path is not None:
            if self.path.exists():
                common = run_git(self.path, "rev-parse", "--git-common-dir").stdout.decode().strip()
                source_common = run_git(self.source, "rev-parse", "--git-common-dir").stdout.decode().strip()
                if (self.path / common).resolve() != (self.source / source_common).resolve():
                    raise WorkspaceError("workspace_repository_mismatch")
                return self
            run_git(self.source, "worktree", "add", "--detach", str(self.path), self.base_commit)
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

    def assert_clean(self, expected_commit: str) -> None:
        """审核和审批绑定提交及干净工作树，不能接受检查后被改写的版本。"""
        if head_commit(self.repository()) != expected_commit:
            raise WorkspaceError("reviewed_commit_changed")
        if run_git(self.repository(), "status", "--porcelain=v1", "--untracked-files=all").stdout.strip():
            raise WorkspaceError("reviewed_tree_changed")

    def release(self) -> None:
        """移除工作区登记，保留目录供事后核对。"""

        if self.path is None:
            return
        run_git(self.source, "worktree", "prune", check=False)

    def changes(self) -> dict:
        """采集工作区相对基线提交的变更；只报告事实，不评价改动。

        基准固定为基线提交而不是当前 HEAD，因此审核产生的提交不会让变更“消失”。
        已知边界：当前版本不解析重命名，重命名会呈现为一次删除加一次新增。
        """

        worktree = self.repository()
        counts = self._counts()
        files = []
        for line in run_git(worktree, "diff", "--name-status", self.base_commit).stdout.decode(
                "utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            code, name = parts[0].strip(), parts[-1]
            additions, deletions, binary = counts.get(name, (0, 0, False))
            files.append({"path": name, "status": code, "additions": additions,
                          "deletions": deletions, "binary": binary})
        for name in self._untracked():
            additions, deletions, binary = self._count_new(name)
            files.append({"path": name, "status": "??", "additions": additions,
                          "deletions": deletions, "binary": binary})
        files.sort(key=lambda item: item["path"])
        return {"base_commit": self.base_commit, "head_commit": head_commit(worktree),
                "worktree": str(worktree), "files": files, "changed_files": len(files),
                "additions": sum(item["additions"] for item in files),
                "deletions": sum(item["deletions"] for item in files)}

    def _counts(self) -> dict:
        """已跟踪文件的增删行数；二进制文件由 git 用 '-' 标注。"""

        counts = {}
        worktree = self.repository()
        output = run_git(worktree, "diff", "--numstat", self.base_commit).stdout.decode("utf-8", errors="replace")
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                added, removed, name = parts
                if added == "-" or removed == "-":
                    counts[name] = (0, 0, True)
                else:
                    counts[name] = (int(added), int(removed), False)
        return counts

    def _untracked(self) -> list[str]:
        """未跟踪文件；这些文件不在 diff 输出里，需要单独枚举。"""

        output = run_git(self.repository(), "status", "--porcelain=v1", "-z", "--untracked-files=all")
        entries = output.stdout.decode("utf-8", errors="replace").split("\0")
        return [entry[3:] for entry in entries if entry.startswith("?? ")]

    def _count_new(self, name: str) -> tuple:
        try:
            data = (self.repository() / name).read_bytes()
        except OSError:
            return (0, 0, False)
        if b"\0" in data:
            return (0, 0, True)
        return (len(data.decode("utf-8", errors="replace").splitlines()), 0, False)

    def diff(self, limit: int = MAX_DIFF_CHARS) -> dict:
        """返回相对基线的总差异文本，按字符上限截断并显式标注。

        计入已提交与未提交的改动，因此审核提交不会让差异消失。
        """

        worktree = self.repository()
        text = run_git(worktree, "diff", "--no-color", "--unified=3",
                       self.base_commit).stdout.decode("utf-8", errors="replace")
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

    def commit(self, message: str) -> dict:
        """把工作区当前状态提交，作为审核可绑定的代码版本。

        没有改动时返回 head 而不新建空提交，这样审核锚点始终对应真实内容版本。
        """

        worktree = self.repository()
        from repopilot.execution.repository import Repository
        paths = run_git(worktree, "diff", "--name-only", "-z", "HEAD").stdout.decode("utf-8").split("\0")
        for path in [p for p in paths if p] + self._untracked():
            try:
                Repository(worktree).resolve(path, must_exist=False)
            except ValueError:
                raise WorkspaceError("sensitive_or_linked_change") from None
        run_git(worktree, "add", "--all")
        staged = run_git(worktree, "diff", "--cached", "--name-only").stdout.decode("utf-8", errors="replace")
        merge_pending = run_git(worktree, 'rev-parse', '-q', '--verify', 'MERGE_HEAD', check=False).returncode == 0
        if not staged.strip() and not merge_pending:
            return {"commit": head_commit(worktree), "created": False, "files": []}
        # linked worktree 默认共享仓库配置；身份只对本次命令生效。
        run_git(worktree, "-c", "user.name=RepoPilot", "-c", "user.email=repopilot@localhost",
                "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", message)
        files = sorted(name for name in staged.splitlines() if name.strip())
        return {"commit": head_commit(worktree), "created": True, "files": files}

    def diff_since(self, commit: str, limit: int = MAX_DIFF_CHARS) -> dict:
        """相对指定提交的差异，用于查看某个审核锚点之后又改了什么。"""

        worktree = self.repository()
        text = run_git(worktree, "diff", "--no-color", "--unified=3",
                       commit).stdout.decode("utf-8", errors="replace")
        return {"diff": text[:limit], "truncated": len(text) > limit, "characters": len(text)}

    def apply_commit(self, commit: str):
        """把子任务提交应用到当前整合工作区但不提交；冲突留给受控整合修复。"""
        return run_git(self.repository(), "cherry-pick", "--no-commit", commit, check=False)

    def integration_conflicts(self) -> list[str]:
        output = run_git(self.repository(), "diff", "--name-only", "--diff-filter=U", check=False)
        return sorted(name for name in output.stdout.decode("utf-8", errors="replace").splitlines() if name)

    def abort_integration(self):
        # --no-commit冲突不一定留下可abort的cherry-pick状态；硬重置到当前父提交确保不把冲突标记留在整合区。
        run_git(self.repository(), "cherry-pick", "--abort", check=False)
        run_git(self.repository(), "reset", "--hard", "HEAD", check=False)
        # 冲突修复阶段可能新建未跟踪文件；它们不属于父候选，清理后才能回到可恢复的干净现场。
        run_git(self.repository(), "clean", "-fd", check=False)

    def finish_integration(self):
        run_git(self.repository(), "cherry-pick", "--quit", check=False)

    def unresolved_markers(self, paths) -> list[str]:
        """冲突标记残留是“模型说已解决”之外可检查的本地事实。"""
        result = []
        for name in paths:
            try:
                text = (self.repository() / name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if any(marker in text for marker in ("<<<<<<<", "=======", ">>>>>>>")):
                result.append(name)
        return sorted(set(result))


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

    固定 HEAD、跟踪文件的实际 diff 和未跟踪文件内容，避免只比较状态字母漏掉重复修改。
    不逐个读取未改变的跟踪文件；忽略的运行数据不纳入此摘要。
    """

    digest = sha256(run_git(repo, "rev-parse", "HEAD").stdout)
    digest.update(run_git(repo, "diff", "--binary", "HEAD").stdout)
    names = run_git(repo, "ls-files", "--others", "--exclude-standard", "-z").stdout
    digest.update(names)
    for name in names.decode("utf-8").split("\0"):
        if name:
            with (repo / name).open("rb") as stream:
                for chunk in iter(lambda: stream.read(1_048_576), b""):
                    digest.update(chunk)
    return digest.hexdigest()
