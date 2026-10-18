"""步骤 2：白名单 pytest 工具的测试。"""

import subprocess
import sys

import pytest

from repopilot.legacy.tools import testing


@pytest.mark.parametrize(
    ("test_target", "expected_code"),
    [
        ("tests/test_blog.py -k update", "unexpected_test_arguments"),
        ("tests/test_blog.py -q -p no:cacheprovider", "unexpected_test_arguments"),
        ("tests/test_blog.py; whoami", "invalid_test_target"),
        ("tests/test_blog.py -q; whoami", "invalid_test_target"),
        ("tests/test_blog.py -q\nwhoami", "invalid_test_target"),
        ("tests/../secret.py", "invalid_test_target"),
        ("tests/../secret.py -q", "invalid_test_target"),
        (r"tests/..\secret.py", "invalid_test_target"),  # Windows 路径也不能逃逸
        ("tests/test_blog.py tests/test_auth.py", "invalid_test_target"),
    ],
)
def test_run_test_rejects_extra_arguments_and_shell_operators(
    tmp_path, monkeypatch, test_target, expected_code
):
    """测试模型不能借 test_target 注入 pytest 参数或 Shell 命令。"""
    def should_not_run(*args, **kwargs):
        raise AssertionError("非法参数不应启动子进程")

    monkeypatch.setattr(testing.subprocess, "run", should_not_run)

    result = testing.run_test(tmp_path, test_target)

    assert result.ok is False
    assert result.error.code == expected_code
    if expected_code == "unexpected_test_arguments":
        assert "本次测试未执行" in result.error.message
        assert "重新调用 run_test" in result.error.message


def test_failed_pytest_is_a_successful_tool_observation(tmp_path, monkeypatch):
    """测试 pytest 返回 1 表示工具成功获得了“测试失败”这一观察。"""
    recorded = {}

    def fake_run(argv, **kwargs):
        recorded["argv"] = argv
        recorded["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            argv,
            returncode=1,
            stdout="1 failed\n",
            stderr="",
        )

    monkeypatch.setattr(testing.subprocess, "run", fake_run)

    target = "tests/test_blog.py::test_update"
    result = testing.run_test(tmp_path, target)

    assert recorded["argv"] == [
        sys.executable,
        "-m",
        "pytest",
        target,
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    assert recorded["kwargs"]["cwd"] == tmp_path
    assert recorded["kwargs"]["shell"] is False
    assert result.ok is True
    assert result.data["returncode"] == 1
    assert result.data["stdout"] == "1 failed\n"


def test_run_test_returns_a_specific_timeout_error(tmp_path, monkeypatch):
    """测试 pytest 超时会返回稳定的 test_timeout 错误码。"""
    def fake_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=30)

    monkeypatch.setattr(testing.subprocess, "run", fake_timeout)

    result = testing.run_test(tmp_path, "tests/test_blog.py")

    assert result.ok is False
    assert result.error.code == "test_timeout"


def test_run_test_converts_process_start_failure_to_tool_error(tmp_path, monkeypatch):
    """测试 pytest 无法启动时不会让整个 Agent 进程崩溃。"""
    def fake_failure(*args, **kwargs):
        raise OSError("cannot start process")

    monkeypatch.setattr(testing.subprocess, "run", fake_failure)

    result = testing.run_test(tmp_path, "tests/test_blog.py")

    assert result.ok is False
    assert result.error.code == "process_start_failed"


def test_run_test_truncates_long_process_output(tmp_path, monkeypatch):
    """测试 pytest 输出不能无限增长并塞进下一轮模型上下文。"""
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="x" * 30000, stderr="")

    monkeypatch.setattr(testing.subprocess, "run", fake_run)
    result = testing.run_test(tmp_path, "tests/test_blog.py")

    assert result.ok is True
    assert result.data["returncode"] == 1
    assert len(result.data["stdout"]) <= 20000
    assert result.truncated is True
