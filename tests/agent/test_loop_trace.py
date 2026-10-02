"""步骤 6 第二组：真实写临时文件、Fake 模型和工具，无网络和模型费用。

使用 agent_loop(..., run_dir=..., task_id=...) 接口；先修 test_trace，再跑本文件。
"""

import json
from datetime import datetime
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest

from repopilot.agent.loop import FakeChatModel, agent_loop
from repopilot.agent.schemas import ModelTurn, ToolCall, ToolError, ToolResult, Usage


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_events(root):
    return [json.loads(line) for line in (root / "trace.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()]


def final_turn():
    return ModelTurn(content=json.dumps({
        "status": "diagnosed", "summary": "定位成功", "root_cause": "条件错误",
        "evidence": [{"source": "app.py:1", "observation": "条件相反"}],
        "proposed_fix": ["修正条件"], "verification": ["运行测试"], "risks": [],
    }, ensure_ascii=False), usage=Usage(prompt_tokens=2, completion_tokens=1, total_tokens=3))


def tool_turn():
    return ModelTurn(tool_calls=[ToolCall(
        id="call-1", name="read_file", arguments_json='{"path":"app.py"}'
    )], usage=Usage(prompt_tokens=2, completion_tokens=1, total_tokens=3))


class FakeRegistry:
    schema = []  # 本文件只测记录行为，工具参数的 Schema 已有独立测试。

    def __init__(self, mode="ok"):
        self.mode = mode

    def execute(self, name, arguments):
        if self.mode == "raises":
            raise RuntimeError("simulated tool error")
        if self.mode == "failed":
            return ToolResult(ok=False, tool=name,
                              error=ToolError(code="file_not_found", message="文件不存在"))
        return ToolResult(ok=True, tool=name, data={"content": "1: 条件相反"})


@pytest.fixture
def settings(tmp_path):
    """完整但无真实凭据的假配置，所有产物都在 pytest 临时目录。"""
    return SimpleNamespace(
        runs_dir=tmp_path / "runs", model="fake-model", api_key="fake-secret-for-test",
        max_steps=5, max_tool_calls=5, max_total_tokens=1000, max_cost=1.0,
        run_timeout_seconds=60, tool_timeout_seconds=30, max_tool_output_chars=20000,
        input_price_per_million=1.0, output_price_per_million=2.0,
    )


def run(settings, turns, registry=None, **kwargs):
    return agent_loop(
        FakeChatModel(turns), settings,
        [{"role": "system", "content": "只读诊断"}, {"role": "user", "content": "查找问题"}],
        registry or FakeRegistry(), run_dir=settings.runs_dir / "test-run",
        task_id="case-a", **kwargs,
    )


def test_completed_run_saves_final_messages_answer_and_summary(settings):
    """测四个产物内容相符，messages 包含最后一轮，而非上轮快照。"""
    result = run(settings, [tool_turn(), final_turn()])
    root = settings.runs_dir / "test-run"

    assert read_json(root / "messages.json") == result.messages
    assert read_json(root / "final_answer.json") == result.answer.model_dump(mode="json")
    summary = read_json(root / "summary.json")
    assert summary["status"] == "completed"
    assert summary["stop_reason"] == "final_answer"
    assert summary["model_steps"] == 2
    assert summary["tool_calls"] == 1
    assert summary["usage"]["total_tokens"] == 6
    assert summary["estimated_cost_cny"] == pytest.approx(0.000008)
    assert summary["elapsed_seconds"] >= 0
    assert summary["error"] is None
    assert summary["final_answer_path"] == "final_answer.json"


def test_trace_event_order_timestamps_and_call_ids(settings):
    """测正常轨迹的事件顺序、单调时间顺序和开始/结束的 call ID 配对。"""
    run(settings, [tool_turn(), final_turn()])
    events = read_events(settings.runs_dir / "test-run")
    # 题目编号与输出目录名 test-run 不同，证明它不是从路径推断的。
    assert events[0]["data"]["task_id"] == "case-a"
    assert [e["event"] for e in events] == [
        "run_started", "model_turn_completed", "tool_call_started",
        "tool_call_completed", "model_turn_completed", "run_finished",
    ]
    times = [datetime.fromisoformat(e["datetime"]) for e in events]
    assert times == sorted(times)
    assert events[2]["data"]["tool_call_id"] == events[3]["data"]["tool_call_id"] == "call-1"
    assert [e["data"]["model_step"] for e in events
            if e["event"] == "model_turn_completed"] == [1, 2]


@pytest.mark.parametrize("mode", ["failed", "raises"])
def test_failed_tool_still_has_result_event_and_summary(settings, mode):
    """测返回失败与抛异常两种情况，都留下工具结束事件和失败 summary。"""
    result = run(settings, [tool_turn()], FakeRegistry(mode))
    root = settings.runs_dir / "test-run"
    summary = read_json(root / "summary.json")
    assert summary["status"] == "failed"
    assert summary["stop_reason"] == "tool_error"
    assert summary["tool_calls"] == 1
    assert summary["error"]["message"]
    assert summary["final_answer_path"] is None
    assert not (root / "final_answer.json").exists()
    events = read_events(root)
    completed = [e for e in events if e["event"] == "tool_call_completed"]
    assert len(completed) == 1
    assert completed[0]["data"]["tool_call_id"] == "call-1"
    assert completed[0]["data"]["result"]["ok"] is False
    assert events[-1]["event"] == "run_finished"
    assert read_json(root / "messages.json") == result.messages
    assert result.messages[-1]["tool_call_id"] == "call-1"


def test_model_error_is_saved_and_config_secret_is_redacted(settings):
    """测 API 异常有 summary，异常文本里意外带入的已知 Key 也不能落盘。"""
    class BrokenModel:
        def complete(self, messages, tools):
            raise RuntimeError("request failed: " + settings.api_key)

    result = agent_loop(BrokenModel(), settings, [], FakeRegistry(),
                        run_dir=settings.runs_dir / "test-run", task_id="case-a")
    root = settings.runs_dir / "test-run"
    summary = read_json(root / "summary.json")
    assert result.stop_reason == "model_error"
    assert summary["error"]["message"]
    assert summary["final_answer_path"] is None
    assert read_events(root)[-1]["event"] == "run_finished"
    for path in root.iterdir():
        if path.is_file():
            assert settings.api_key not in path.read_text(encoding="utf-8")


def test_invalid_final_answer_has_no_answer_file(settings):
    """测两次格式失败都留在 Trace，且不生成成功答案文件。"""
    bad = ModelTurn(content="not json", usage=Usage())
    result = run(settings, [bad, bad])
    root = settings.runs_dir / "test-run"
    assert read_json(root / "summary.json")["stop_reason"] == "invalid_final_answer"
    assert read_json(root / "messages.json") == result.messages
    assert not (root / "final_answer.json").exists()
    model_turns = [event for event in read_events(root) if event["event"] == "model_turn_completed"]
    assert [event["data"]["final_answer_recovery"] for event in model_turns] == [False, True]


def test_step_limit_summary_counts_received_response_usage(settings):
    """测达到步数上限也要记录已经发生的 Token 消耗。"""
    settings.max_steps = 1
    run(settings, [tool_turn()])
    summary = read_json(settings.runs_dir / "test-run" / "summary.json")
    assert summary["stop_reason"] == "max_steps"
    assert summary["model_steps"] == 1
    assert summary["usage"]["total_tokens"] == 3


def test_timeout_after_model_response_still_counts_usage(settings):
    """测调用中超时的响应仍被统计；假时钟可被任意读取，不受调用次数影响。"""
    now = [0.0]

    class SlowModel:
        def complete(self, messages, tools):
            now[0] = 100.0
            return final_turn()

    agent_loop(SlowModel(), settings, [], FakeRegistry(),
               clock=lambda: now[0], run_dir=settings.runs_dir / "test-run", task_id="case-a")
    summary = read_json(settings.runs_dir / "test-run" / "summary.json")
    assert summary["stop_reason"] == "run_timeout"
    assert summary["model_steps"] == 1
    assert summary["usage"]["total_tokens"] == 3


def test_disabled_trace_does_not_create_run_directory(settings):
    """测 run_dir=None 仍可正常运行，且不需要创建保存目录。"""
    result = agent_loop(FakeChatModel([final_turn()]), settings, [], FakeRegistry())
    assert result.status == "completed"
    assert not settings.runs_dir.exists()


def test_explicit_run_directory_is_used_without_inferring_task_id(settings, tmp_path):
    """输出目录由调用方决定；不传 task_id 时记录 null，不猜目录名。"""
    output = tmp_path / "custom-output" / "attempt"
    agent_loop(FakeChatModel([final_turn()]), settings, [], FakeRegistry(), run_dir=output)

    assert read_json(output / "summary.json")["status"] == "completed"
    assert read_events(output)[0]["data"]["task_id"] is None
    assert not settings.runs_dir.exists()


def test_trace_remains_readable_after_workspace_cleanup(settings, tmp_path):
    """测产物位于运行目录，不依赖已清理的临时工作区。"""
    from pathlib import Path

    with TemporaryDirectory(dir=tmp_path) as directory:
        workspace = Path(directory)
        (workspace / "app.py").write_text("条件相反", encoding="utf-8")

        class ReadingRegistry(FakeRegistry):
            def execute(self, name, arguments):
                return ToolResult(ok=True, tool=name, data={
                    "content": (workspace / "app.py").read_text(encoding="utf-8")
                })

        run(settings, [tool_turn(), final_turn()], ReadingRegistry())

    assert not workspace.exists()
    events = read_events(settings.runs_dir / "test-run")
    observation = next(e for e in events if e["event"] == "tool_call_completed")
    assert observation["data"]["result"]["data"]["content"] == "条件相反"
