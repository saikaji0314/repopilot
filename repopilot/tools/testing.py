# 白名单 pytest
import subprocess
from pathlib import Path
import re
import sys
from repopilot.agent.schemas import ToolResult, ToolError
from repopilot.repository_runtime import workspace_test_env


def invalid_target() -> ToolResult:
    return ToolResult(
        ok=False,
        tool="run_test",
        data={},
        error=ToolError(code="invalid_test_target", message="test_target must be tests, tests/... or tests/...::test_name"),
        truncated=False
    )


def run_test(workspace_path: Path, test_target: str, max_tool_output_chars: int = 10000,
             python_executable: Path | None = None) -> ToolResult:
    # 校验test_target只允许 tests、tests/... 或 tests/...::test_name
    if any(token in test_target for token in (";", "|", "&", "`", "$", "<", ">", "\n", "\r")):
        return invalid_target()
    # 拆分只用于识别错误，绝不把“去掉参数的目标”直接拿去执行。
    target_parts = test_target.split(maxsplit=1)
    target = target_parts[0] if target_parts else ""
    parts = re.split(r"[/\\]", target.split("::", 1)[0])
    if not parts or parts[0] != "tests" or ".." in parts:  # tests/../secret.py
        return invalid_target()
    if any(char.isspace() for char in test_target):
        if len(target_parts) == 2 and target_parts[1].startswith("-"):
            return ToolResult(
                ok=False,
                tool="run_test",
                data={},
                error=ToolError(
                    code="unexpected_test_arguments",
                    message=(
                        "本次测试未执行：test_target 不能包含命令行参数。"
                        "请只传测试目录、文件或 node ID，例如 tests/test_blog.py::test_update。"
                        "程序会自动添加 -q -p no:cacheprovider，请修正参数后重新调用 run_test。"
                    ),
                ),
            )
        return invalid_target()
    # 执行 pytest 命令
    try:
        completed = subprocess.run(
            [
                str(python_executable or sys.executable),
                "-m",
                "pytest", 
                test_target, 
                "-q", 
                "-p", 
                "no:cacheprovider"
            ], 
            cwd=workspace_path,
            shell=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
            check=False,
            env=workspace_test_env(workspace_path),
        )
    except subprocess.TimeoutExpired as e:
        return ToolResult(
            ok=False,
            tool="run_test",
            data={},
            error=ToolError(code="test_timeout", message=f"pytest timeout"),
            truncated=False
        )
    except OSError as e:
        return ToolResult(
            ok=False,
            tool="run_test",
            data={},
            error=ToolError(code="process_start_failed", message=f"pytest failed"),
            truncated=False
        )

    truncated = False
    if len(completed.stdout) > max_tool_output_chars:
        completed.stdout = completed.stdout[:max_tool_output_chars]
        truncated = True
    if len(completed.stderr) > max_tool_output_chars:
        completed.stderr = completed.stderr[:max_tool_output_chars]
        truncated = True
    # 统一返回外壳
    return ToolResult(
        ok=True,
        tool="run_test",
        data={
            "returncode": completed.returncode, 
            "stdout": completed.stdout, 
            "stderr": completed.stderr
        },
        error=None,
        truncated=truncated
    )
