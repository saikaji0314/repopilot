"""步骤 8：CLI 参数和退出码；main(argv) 是本次建议新增的薄入口。"""

import runpy
import sys

import pytest

from repopilot import agent_runner as runner


def get_main():
    main = getattr(runner, "main", None)
    assert callable(main), "请在 agent_runner.py 增加 main(argv=None) CLI 入口"
    return main


@pytest.mark.parametrize("argv, expected", [
    (["--task", "case-a", "--task", "case-b", "--repetitions", "2"],
         dict(task_ids=["case-a", "case-b"], repetitions=2, retrieval_mode="symbols")),
        (["--all"], dict(task_ids=None, repetitions=None, retrieval_mode="symbols")),
        (["--task", "case-a", "--retrieval-mode", "code"],
         dict(task_ids=["case-a"], repetitions=None, retrieval_mode="code")),
])
def test_cli_passes_selection_to_runner(monkeypatch, argv, expected):
    """测 --task 可重复、--all 表示全选，成功退出码为 0。"""
    calls = []
    monkeypatch.setattr(runner, "run_agent", lambda **kwargs: calls.append(kwargs) or 0)
    with pytest.raises(SystemExit) as exc:
        get_main()(argv)
    assert exc.value.code == 0
    assert calls == [expected]


@pytest.mark.parametrize("argv", [[], ["--all", "--task", "case-a"]])
def test_cli_requires_exactly_one_selection(monkeypatch, argv):
    """测必须明确选择任务，不能同时指定 --all 和 --task。"""
    calls = []
    monkeypatch.setattr(runner, "run_agent", lambda **kwargs: calls.append(kwargs))
    with pytest.raises(SystemExit) as exc:
        get_main()(argv)
    assert exc.value.code == 2
    assert calls == []


def test_script_propagates_failed_exit_code(monkeypatch):
    """测脚本真正向操作系统返回 1，不能只打印失败退出码。"""
    monkeypatch.setattr(runner, "run_agent", lambda **kwargs: 1)
    monkeypatch.setattr(sys, "argv", ["run_agent", "--task", "case-a"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("scripts.run_agent", run_name="__main__")
    assert exc.value.code == 1
