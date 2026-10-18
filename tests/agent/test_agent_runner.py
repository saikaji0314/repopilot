"""步骤 8：只测编排；配置、模型、预检、工作区和 Loop 都用本地替身。"""

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS

import pytest

from repopilot.legacy import agent_runner as runner
from repopilot.legacy.agent.schemas import AgentResult, AgentStatus, StopReason, Usage


@pytest.fixture
def env(tmp_path, monkeypatch):
    tasks_dir = tmp_path / "tasks"
    for task_id in ["case-a", "case-b"]:
        (tasks_dir / task_id).mkdir(parents=True)
    settings = NS(tasks_dir=tasks_dir, repo_dir=tmp_path / "clean",
                  runs_dir=tmp_path / "runs", timeout_seconds=30,
                  repetitions_per_task=1, system_prompt="只读诊断")
    state = NS(settings=settings, calls=[], workspaces=[], model_count=0,
               clean_error=False, defect_error=False, loop_raises=False,
               status=AgentStatus.COMPLETED, task_snapshots=[])
    state.clean_repositories = []
    state.workspace_sources = []

    def load(**kwargs):
        return settings

    def clean(*args, **kwargs):
        state.clean_repositories.append((args[0], kwargs.get("python_executable")))
        if state.clean_error:
            raise RuntimeError("clean preflight failed")
        return NS(stdout="1 passed", returncode=0)

    def defect(*args, **kwargs):
        if state.defect_error:
            raise RuntimeError("defect preflight failed")
        return NS(stdout="1 failed", returncode=1)

    def task(path):
        return dict(id=path.parent.name, schema_version="1.0", title="测试任务",
                    task_type="diagnosis", repository={}, user_request=path.parent.name,
                    initial_evidence=dict(command="pytest", summary="1 failed", failures=[]),
                    constraints=dict(mode="read_only", max_steps=5, timeout_seconds=30),
                    required_output=dict(fields={}), _task_dir=path.parent)

    @contextmanager
    def workspace(*args):
        state.workspace_sources.append(args[0])
        with TemporaryDirectory(dir=tmp_path) as directory:
            path = Path(directory)
            state.workspaces.append(path)
            yield path

    def model(*args, **kwargs):
        state.model_count += 1
        return object()

    def loop(chat_model, settings, initial_messages, tool_registry, **kwargs):
        # 接收完整路径，不再与 settings.runs_dir 拼接。
        root = kwargs["run_dir"]
        print(list(root.glob('**/*')))
        print(root)
        state.calls.append(dict(root=root, messages=deepcopy(initial_messages),
                                workspace=tool_registry.workspace, task_id=kwargs["task_id"],
                                test_python=next(t.python_executable for t in tool_registry.tools
                                                 if t.name == "run_test")))
        snapshot = root / "task.json"
        state.task_snapshots.append(json.loads(snapshot.read_text(encoding="utf-8"))
                                    if snapshot.exists() else None)
        if state.loop_raises:
            raise RuntimeError("unexpected loop failure")
        return AgentResult(status=state.status,
                           stop_reason=StopReason.FINAL_ANSWER if state.status == AgentStatus.COMPLETED
                           else StopReason.MODEL_ERROR,
                           usage=Usage(), messages=initial_messages)

    monkeypatch.setattr(runner, "load_agent_settings", load)
    monkeypatch.setattr(runner, "verify_clean_repository", clean)
    # 这是计划补上的预检入口；raising=False 让尚未导入它的草稿也可收集测试。
    monkeypatch.setattr(runner, "verify_task_defect", defect, raising=False)
    monkeypatch.setattr(runner, "parse_task", task)
    monkeypatch.setattr(runner, "prepared_task_workspace", workspace)
    monkeypatch.setattr(runner, "BailianChatModel", model)
    monkeypatch.setattr(runner, "agent_loop", loop)
    return state


def test_runner_selects_argument_task_and_saves_visible_task_before_loop(env):
    """测使用 task_ids 入参；task.json 在 Loop 前保存且不含内部 Path 字段。"""
    assert runner.run_agent(task_ids=["case-b"], repetitions=1) == 0
    assert len(env.calls) == 1
    assert env.calls[0]["task_id"] == "case-b"
    assert "case-b" in env.calls[0]["messages"][1]["content"]
    assert env.task_snapshots[0]["id"] == "case-b"
    assert "_task_dir" not in env.task_snapshots[0]
    assert not env.workspaces[0].exists()


def test_all_tasks_and_repetitions_use_distinct_output_directories(env):
    """测两个任务各两次都执行，四个结果目录互不覆盖。"""
    assert runner.run_agent(task_ids=None, repetitions=2) == 0
    assert len(env.calls) == 4
    assert len({c["root"] for c in env.calls}) == 4
    assert sorted(t["id"] for t in env.task_snapshots) == ["case-a", "case-a", "case-b", "case-b"]
    assert all(not path.exists() for path in env.workspaces)


def test_runner_uses_each_tasks_repository_and_test_python(env, monkeypatch):
    """不同仓库分别预检、复制，并将对应解释器传给真实测试工具。"""
    repos = {"case-a": (Path("first-repo"), None),
             "case-b": (Path("second-repo"), Path("isolated-python.exe"))}
    monkeypatch.setattr(runner, "resolve_repository", lambda task, default: repos[task["id"]])
    assert runner.run_agent(task_ids=None, repetitions=2) == 0
    assert env.clean_repositories == list(repos.values())
    assert env.workspace_sources == [Path("first-repo")] * 2 + [Path("second-repo")] * 2
    assert [call["test_python"] for call in env.calls] == [None, None] + [Path("isolated-python.exe")] * 2


@pytest.mark.parametrize("count", [0, -1])
def test_invalid_repetitions_rejected_before_model_creation(env, count):
    """测 0 不能通过 repetitions or default 悄悄退回默认值。"""
    with pytest.raises(ValueError, match="repetitions"):
        runner.run_agent(task_ids=["case-a"], repetitions=count)
    assert env.model_count == 0


def test_unknown_task_rejected_before_model_creation(env):
    """测拼错任务 ID 不会启动模型。"""
    with pytest.raises(ValueError, match="未知任务"):
        runner.run_agent(task_ids=["missing"])
    assert env.model_count == 0


def test_failed_agent_returns_nonzero_and_cleans_workspace(env):
    """测按 AgentResult.status 判定结果，而非不存在的 success 属性。"""
    env.status = AgentStatus.FAILED
    assert runner.run_agent(task_ids=["case-a"]) == 1
    assert len(env.calls) == 1
    assert all(not path.exists() for path in env.workspaces)


def test_unexpected_loop_error_still_cleans_workspace(env):
    """测 Loop 意外报错由运行器转成失败退出码，with 仍清理临时目录。"""
    env.loop_raises = True
    assert runner.run_agent(task_ids=["case-a"]) == 1
    assert len(env.workspaces) == 1
    assert not env.workspaces[0].exists()


@pytest.mark.parametrize("stage", ["clean_error", "defect_error"])
def test_preflight_failure_never_calls_loop(env, stage):
    """测干净仓库/缺陷证据预检失败时不会进入 Agent 调用。"""
    setattr(env, stage, True)
    assert runner.run_agent(task_ids=["case-a"]) == 1
    assert env.calls == []
    assert all(not path.exists() for path in env.workspaces)


def test_code_mode_never_builds_index_and_removes_symbol_references(env, monkeypatch):
    """测四工具组没有索引开销，schema 和用户提示也不包含符号工具。"""
    def forbidden_index(*args):
        pytest.fail("四工具模式不应该建立索引")

    monkeypatch.setattr(runner, "build_index", forbidden_index)
    assert runner.run_agent(task_ids=["case-a"], retrieval_mode="code") == 0
    root = env.calls[0]["root"]
    runtime = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
    assert len(runtime["tools"]) == 4
    assert "search_symbols" not in json.dumps(runtime, ensure_ascii=False)
    assert runtime["index_seconds"] == 0


def test_symbols_mode_builds_index_and_snapshot_excludes_credentials(env, monkeypatch):
    """测五工具组建一次索引，快照保留实际 schema 但不保存凭据。"""
    real_index = runner.build_index
    calls = []
    def count_index(workspace):
        calls.append(workspace)
        return real_index(workspace)
    monkeypatch.setattr(runner, "build_index", count_index)
    env.settings.api_key = "secret-test-key"
    env.settings.workspace_id = "secret-workspace"
    assert runner.run_agent(task_ids=["case-a"]) == 0
    runtime_text = (env.calls[0]["root"] / "runtime.json").read_text(encoding="utf-8")
    assert len(calls) == 1
    assert len(json.loads(runtime_text)["tools"]) == 5
    assert "secret-test-key" not in runtime_text
    assert "secret-workspace" not in runtime_text
