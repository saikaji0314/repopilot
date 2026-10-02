"""多仓库选择与 src 导入隔离，不依赖网络或模型。"""
import os
from pathlib import Path

import pytest

from repopilot import repository_runtime as runtime
from repopilot.tools import testing


def test_selects_declared_repository_not_global_default(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "PROJECT_ROOT", tmp_path)
    actual = tmp_path / "target-repos" / "second"
    actual.mkdir(parents=True)
    repo, python = runtime.resolve_repository(
        {"repository": {"baseline_path": "target-repos/second"}}, tmp_path / "first")
    assert repo == actual.resolve()
    assert python is None


@pytest.mark.parametrize("path", ["../outside", "target-repos/../private", "target-repos"])
def test_repository_cannot_escape_allowed_root(tmp_path, monkeypatch, path):
    monkeypatch.setattr(runtime, "PROJECT_ROOT", tmp_path)
    with pytest.raises(ValueError):
        runtime.resolve_repository({"repository": {"baseline_path": path}}, tmp_path)


def test_src_env_is_local_and_does_not_change_parent_environment(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    monkeypatch.setenv("PYTHONPATH", "previous-path")
    env = runtime.workspace_test_env(tmp_path)
    assert env["PYTHONPATH"] == str(tmp_path / "src") + os.pathsep + "previous-path"
    assert os.environ["PYTHONPATH"] == "previous-path"


def test_test_tool_uses_selected_python_and_workspace_src(tmp_path, monkeypatch):
    from subprocess import CompletedProcess
    (tmp_path / "src").mkdir()
    calls = []
    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return CompletedProcess(argv, 0, stdout="1 passed", stderr="")
    monkeypatch.setattr(testing.subprocess, "run", fake_run)
    python = tmp_path / "test-python.exe"
    assert testing.run_test(tmp_path, "tests", python_executable=python).ok
    assert calls[0][0][0] == str(python)
    assert calls[0][1]["env"]["PYTHONPATH"].split(os.pathsep)[0] == str(tmp_path / "src")
