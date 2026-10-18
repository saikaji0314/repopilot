"""运行干净基线与任务缺陷测试，不通过 Shell 解释命令。"""

from dataclasses import asdict, dataclass
from pathlib import Path
import shlex
import subprocess
import sys
from repopilot.legacy.repository_runtime import workspace_test_env


@dataclass(frozen=True)
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    def to_dict(self) -> dict:
        return asdict(self)


def run_command(command: str, cwd: Path, *, timeout_seconds: int,
                python_executable: Path | None = None) -> CommandResult:
    argv = shlex.split(command)
    if not argv:
        raise ValueError("测试命令不能为空")
    if argv[0].lower() in {"python", "python.exe", "python3"}:
        argv[0] = str(python_executable or sys.executable)

    completed = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout_seconds,
        check=False,
        env=workspace_test_env(cwd),
    )
    return CommandResult(
        argv=argv,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def verify_clean_repository(repo_dir: Path, *, timeout_seconds: int,
                            python_executable: Path | None = None) -> CommandResult:
    result = run_command(
        "python -m pytest -q -p no:cacheprovider",
        repo_dir,
        timeout_seconds=timeout_seconds,
        python_executable=python_executable,
    )
    if result.returncode != 0:
        detail = (result.stdout + "\n" + result.stderr).strip()
        raise RuntimeError(f"干净基线测试失败，停止评测:\n{detail}")
    return result


def verify_task_defect(task: dict, repo_dir: Path, *,
                       python_executable: Path | None = None) -> CommandResult:
    evidence = task["initial_evidence"]
    timeout = int(task["constraints"]["timeout_seconds"])
    result = run_command(evidence["command"], repo_dir, timeout_seconds=timeout,
                         python_executable=python_executable)
    combined = result.stdout + "\n" + result.stderr

    if result.returncode == 0:
        raise RuntimeError(f"任务 {task['id']} 注入后测试仍通过，拒绝调用 LLM")
    if str(evidence["summary"]) not in combined:
        raise RuntimeError(
            f"任务 {task['id']} 的真实失败摘要与 task.yaml 不一致。"
            f"期望包含: {evidence['summary']}\n实际输出:\n{combined.strip()}"
        )
    return result
