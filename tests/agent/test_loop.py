"""步骤 5：用 Fake 模型测试 Agent Loop；全部为本地测试，不花模型费用。"""

import json
import subprocess
from types import SimpleNamespace

import pytest

from repopilot.legacy.agent.loop import FakeChatModel, agent_loop
from repopilot.legacy.agent.schemas import (
    AgentStatus,
    ModelTurn,
    StopReason,
    ToolCall,
    ToolError,
    ToolResult,
    Usage,
)
from repopilot.legacy.schemas import BaselineAnswer
from repopilot.legacy.tools import testing
from repopilot.legacy.tools.base import RunTestTool, SearchCodeTool, ToolRegistry


def _settings(**changes):
    """只保留循环用到的配置，避免测试依赖密钥和真实仓库。"""
    values = dict(
        api_key="test-only-key",  # 脱敏逻辑使用；假凭据，不读取 .env。
        max_steps=5,
        max_tool_calls=5,
        max_total_tokens=1000,
        max_cost=1.0,
        run_timeout_seconds=60,
        input_price_per_million=1.0,
        output_price_per_million=1.0,
    )
    values.update(changes)
    return SimpleNamespace(**values)


def _messages():
    return [
        {"role": "system", "content": "只读诊断"},
        {"role": "user", "content": "定位缺陷"},
    ]


def _tool_turn(call_id="call-1", name="search_code", arguments='{"query":"x"}'):
    return ModelTurn(
        content="先查找相关代码",
        tool_calls=[ToolCall(id=call_id, name=name, arguments_json=arguments)],
        finish_reason="tool_calls",
        usage=Usage(prompt_tokens=2, completion_tokens=1, total_tokens=3),
    )


def _final_turn():
    answer = BaselineAnswer(
        status="diagnosed",
        summary="已定位缺陷",
        root_cause="条件判断错误",
        evidence=[{"source": "app.py:3", "observation": "分支条件反了"}],
        proposed_fix=["修正条件判断"],
        verification=["运行相关测试"],
        risks=["检查相邻分支"],
    )
    return ModelTurn(
        content=answer.model_dump_json(),
        finish_reason="stop",
        usage=Usage(prompt_tokens=4, completion_tokens=2, total_tokens=6),
    )


class FakeRegistry:
    """只记录调用和返回观察；工具注册表的参数校验由 test_base 单测。"""

    schema = [{"type": "function", "function": {"name": "search_code"}}]

    def __init__(self, error_code=None, raises=False):
        self.calls = []
        self.error_code = error_code
        self.raises = raises

    def execute(self, name, arguments):
        self.calls.append((name, arguments))
        if self.raises:
            raise RuntimeError("tool crashed")
        if self.error_code:
            return ToolResult(
                ok=False,
                tool=name,
                error=ToolError(code=self.error_code, message="工具调用失败"),
            )
        return ToolResult(ok=True, tool=name, data={"observation": "found"})


def test_loop_records_tool_call_and_matching_tool_result_before_next_model_turn():
    """测试 assistant tool_calls 和 role=tool 按同一个 call ID 配对。"""
    model = FakeChatModel([_tool_turn(), _final_turn()])
    registry = FakeRegistry()

    result = agent_loop(model, _settings(), _messages(), registry)

    assert result.status == AgentStatus.COMPLETED
    assert result.stop_reason == StopReason.FINAL_ANSWER
    assert isinstance(result.answer, BaselineAnswer)
    assert result.model_steps == 2
    assert result.tool_calls == 1
    assert result.usage.total_tokens == 9
    assert registry.calls == [("search_code", '{"query":"x"}')]
    assert model.received_tools == [registry.schema, registry.schema]
    second_request = model.received_messages[1]
    assert second_request[-2]["role"] == "assistant" 
    assert second_request[-2]["tool_calls"][0]["id"] == "call-1"
    assert second_request[-1]["role"] == "tool"
    assert second_request[-1]["tool_call_id"] == "call-1"
    assert result.messages[-1]["role"] == "assistant"


def test_loop_accepts_direct_valid_final_answer():
    """测试模型第一轮就给合格最终答案时，不会调用工具。"""
    registry = FakeRegistry()
    result = agent_loop(FakeChatModel([_final_turn()]), _settings(), _messages(), registry)

    assert result.status == AgentStatus.COMPLETED
    assert result.tool_calls == 0
    assert registry.calls == []


@pytest.mark.parametrize(
    ("error_code", "expected_reason"),
    [
        ("unknown_tool", StopReason.UNKNOWN_TOOL),
        ("invalid_tool_arguments", StopReason.INVALID_TOOL_ARGUMENTS),
        ("tool_error", StopReason.TOOL_ERROR),
        ("file_not_found", StopReason.TOOL_ERROR),
        ("test_timeout", StopReason.TOOL_ERROR),
        ("new_tool_error", StopReason.TOOL_ERROR),  # 新增工具错误也应安全归类
    ],
)
def test_loop_maps_tool_result_errors_to_failure_reasons(error_code, expected_reason):
    """测试通用错误保留名称，具体工具错误归为 TOOL_ERROR。"""
    result = agent_loop(
        FakeChatModel([_tool_turn()]),
        _settings(),
        _messages(),
        FakeRegistry(error_code=error_code),
    )

    assert result.status == AgentStatus.FAILED
    assert result.stop_reason == expected_reason


def test_loop_catches_unexpected_registry_exception():
    """测试注册表意外抛异常时，循环仍能给出终态。"""
    result = agent_loop(
        FakeChatModel([_tool_turn()]), _settings(), _messages(), FakeRegistry(raises=True)
    )

    assert result.status == AgentStatus.FAILED
    assert result.stop_reason == StopReason.TOOL_ERROR


def test_loop_rejects_invalid_final_json():
    """测试两次都不是 JSON 时，纠错至多一次并以格式错误终止。"""
    bad_turn = ModelTurn(content="not json", usage=Usage(total_tokens=1))
    model = FakeChatModel([bad_turn, bad_turn])
    result = agent_loop(model, _settings(), _messages(), FakeRegistry())

    assert result.status == AgentStatus.FAILED
    assert result.stop_reason == StopReason.INVALID_FINAL_ANSWER
    assert result.answer is None
    assert result.model_steps == 2
    assert result.usage.total_tokens == 2
    assert model.received_tool_choices == [None, "none"]
    assert model.received_messages[1] == _messages()  # 纠错请求不带入无效答案
    assert [message["content"] for message in result.messages[-2:]] == ["not json", "not json"]


def test_loop_recovers_invalid_final_answer_once():
    """测试工具调查后答案格式不合格时，禁止工具的第二轮可以完成。"""
    bad = ModelTurn(content="```json\n{}\n```", usage=Usage(total_tokens=2))
    model = FakeChatModel([_tool_turn(), bad, _final_turn()])
    registry = FakeRegistry()

    result = agent_loop(model, _settings(), _messages(), registry)

    assert result.status == AgentStatus.COMPLETED
    assert result.model_steps == 3
    assert result.tool_calls == 1
    assert result.usage.total_tokens == 11
    assert model.received_tool_choices == [None, None, "none"]
    assert model.received_messages[2] == model.received_messages[1]
    assert result.messages[-2]["content"] == bad.content  # 原始失败留在运行记录
    assert result.answer is not None
    assert registry.calls == [("search_code", '{"query":"x"}')]


def test_loop_recovery_does_not_execute_an_unexpected_tool_call():
    """测试服务端忽略 tool_choice=none 时，纠错轮的工具请求不会执行。"""
    bad = ModelTurn(content="not json", usage=Usage(total_tokens=1))
    registry = FakeRegistry()
    model = FakeChatModel([bad, _tool_turn()])

    result = agent_loop(model, _settings(), _messages(), registry)

    assert result.status == AgentStatus.FAILED
    assert result.stop_reason == StopReason.INVALID_FINAL_ANSWER
    assert result.tool_calls == 0
    assert registry.calls == []
    assert model.received_tool_choices == [None, "none"]


@pytest.mark.parametrize(
    ("limits", "reason"),
    [
        ({"max_steps": 1}, StopReason.MAX_STEPS),
        ({"max_total_tokens": 1}, StopReason.TOKEN_BUDGET),
        ({"input_price_per_million": 1_000_000, "max_cost": 1}, StopReason.COST_BUDGET),
    ],
)
def test_loop_skips_final_recovery_when_budget_is_exhausted(limits, reason):
    """测试首次格式错误后预算已满，不发起额外付费请求。"""
    bad = ModelTurn(content="not json", usage=Usage(prompt_tokens=1, total_tokens=1))
    model = FakeChatModel([bad, _final_turn()])

    result = agent_loop(model, _settings(**limits), _messages(), FakeRegistry())

    assert result.status == AgentStatus.LIMIT_REACHED
    assert result.stop_reason == reason
    assert len(model.received_messages) == 1
    assert result.model_steps == 1


def test_loop_stops_before_exceeding_model_step_limit():
    """测试一步是一轮模型响应，下一轮请求前检查上限。"""
    model = FakeChatModel([_tool_turn(), _final_turn()])
    result = agent_loop(model, _settings(max_steps=1), _messages(), FakeRegistry())

    assert result.status == AgentStatus.LIMIT_REACHED
    assert result.stop_reason == StopReason.MAX_STEPS
    assert result.model_steps == 1
    assert len(model.received_messages) == 1


def test_loop_stops_before_executing_tool_call_above_limit():
    """测试单轮模型请求多个工具时，不能执行超过次数上限的工具。"""
    turn = _tool_turn()
    turn.tool_calls.append(
        ToolCall(id="call-2", name="search_code", arguments_json='{"query":"y"}')
    )
    registry = FakeRegistry()
    result = agent_loop(
        FakeChatModel([turn]), _settings(max_tool_calls=1), _messages(), registry
    )

    assert result.status == AgentStatus.LIMIT_REACHED
    assert result.stop_reason == StopReason.MAX_TOOL_CALLS
    assert len(registry.calls) <= 1


def test_loop_stops_on_token_budget_before_executing_tools():
    """测试模型响应已耗尽 Token 预算时，不再执行其工具请求。"""
    registry = FakeRegistry()
    result = agent_loop(
        FakeChatModel([_tool_turn()]),
        _settings(max_total_tokens=2),
        _messages(),
        registry,
    )

    assert result.status == AgentStatus.LIMIT_REACHED
    assert result.stop_reason == StopReason.TOKEN_BUDGET
    assert registry.calls == []


def test_loop_stops_on_estimated_cost_budget():
    """测试费用按累计 prompt/completion Token 分别估算。"""
    registry = FakeRegistry()
    result = agent_loop(
        FakeChatModel([_tool_turn()]),
        _settings(input_price_per_million=1_000_000, max_cost=1.0),
        _messages(),
        registry,
    )

    assert result.status == AgentStatus.LIMIT_REACHED
    assert result.stop_reason == StopReason.COST_BUDGET
    assert registry.calls == []


def test_loop_stops_on_run_timeout_without_calling_model():
    """测试注入假时钟，快速验证总运行时间限制而不真的等待。"""
    times = iter([0.0, 2.0])
    model = FakeChatModel([_final_turn()])

    result = agent_loop(
        model,
        _settings(run_timeout_seconds=1),
        _messages(),
        FakeRegistry(),
        # Trace 收尾还会读取时钟；后续读取保持在第 2 秒，不抛 StopIteration。
        clock=lambda: next(times, 2.0),
    )

    assert result.status == AgentStatus.LIMIT_REACHED
    assert result.stop_reason == StopReason.RUN_TIMEOUT
    assert model.received_messages == []


def test_loop_converts_model_api_exception_to_failed_status():
    """测试模型接口报错时不会只留下未捕获异常和 RUNNING 状态。"""
    class BrokenModel:
        def complete(self, messages, tools):
            raise RuntimeError("API unavailable")

    result = agent_loop(BrokenModel(), _settings(), _messages(), FakeRegistry())

    assert result.status == AgentStatus.FAILED
    assert result.stop_reason == StopReason.MODEL_ERROR


@pytest.fixture
def test_registry(tmp_path, monkeypatch):
    """使用真实参数校验，只替换 pytest 子进程；记录实际执行的目标。"""
    executed = []

    def fake_run(argv, **kwargs):
        executed.append(argv[3])
        return subprocess.CompletedProcess(argv, 1, stdout="1 failed", stderr="")

    monkeypatch.setattr(testing.subprocess, "run", fake_run)
    return ToolRegistry(tmp_path, [RunTestTool()]), executed


def _test_turn(target="tests/test_blog.py -q -p no:cacheprovider", call_id="call-bad"):
    return _tool_turn(call_id, "run_test", json.dumps({"test_target": target}))


def test_loop_recovers_once_and_keeps_error_in_messages_and_trace(test_registry, tmp_path):
    """测错误→模型改参数→执行→答案；没有偷偷执行原来的非法输入。"""
    registry, executed = test_registry
    model = FakeChatModel([
        _test_turn(),
        _test_turn("tests/test_blog.py", "call-fixed"),
        _final_turn(),
    ])
    result = agent_loop(
        model, _settings(model="fake"), _messages(), registry, run_dir=tmp_path,
    )

    assert result.status == AgentStatus.COMPLETED
    assert executed == ["tests/test_blog.py"]
    assert result.model_steps == 3
    assert result.tool_calls == 2  # 被拒绝的调用也占预算
    assert result.usage.total_tokens == 12
    observation = model.received_messages[1][-1]
    assert observation["tool_call_id"] == "call-bad"
    error_result = json.loads(observation["content"])
    assert error_result["error"]["code"] == "unexpected_test_arguments"
    assert "重新调用 run_test" in error_result["error"]["message"]
    events = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    results = [event["data"]["result"] for event in events if event["event"] == "tool_call_completed"]
    assert [item["ok"] for item in results] == [False, True]
    assert results[0] == error_result  # Trace 仍保存失败，而不是覆盖为成功


@pytest.mark.parametrize("with_success_between", [False, True])
def test_loop_second_argument_error_stops_even_after_a_success(test_registry, with_success_between):
    """测同种错误的机会不重置：重复犯错即停止，中间成功也不重置。"""
    registry, executed = test_registry
    turns = [_test_turn()]
    if with_success_between:
        turns.append(_test_turn("tests/test_blog.py", "call-good"))
    turns.extend([_test_turn(call_id="call-bad-again"), _final_turn()])
    model = FakeChatModel(turns)

    result = agent_loop(model, _settings(), _messages(), registry)

    assert result.status == AgentStatus.FAILED
    assert result.stop_reason == StopReason.TOOL_ERROR
    assert result.model_steps == 2 + int(with_success_between)
    assert len(model.received_messages) == result.model_steps
    assert executed == (["tests/test_blog.py"] if with_success_between else [])


def test_loop_completes_tool_batch_before_asking_model_to_correct(test_registry):
    """测同轮两次调用：不能只回传第一个错误就发起下一轮模型请求。"""
    registry, executed = test_registry
    turn = _test_turn()
    turn.tool_calls.extend(_test_turn("tests/test_auth.py", "call-other").tool_calls)
    model = FakeChatModel([turn, _test_turn("tests/test_blog.py", "call-fixed"), _final_turn()])

    result = agent_loop(model, _settings(), _messages(), registry)

    assert result.status == AgentStatus.COMPLETED
    observations = model.received_messages[1][-2:]
    assert [item["role"] for item in observations] == ["tool", "tool"]
    assert [item["tool_call_id"] for item in observations] == ["call-bad", "call-other"]
    assert executed == ["tests/test_auth.py", "tests/test_blog.py"]


@pytest.mark.parametrize("target", ["tests/../secret.py -q", "tests/test_blog.py -q; whoami"])
def test_loop_does_not_recover_unsafe_test_targets(test_registry, target):
    """测路径逃逸和 Shell 拼接仍然立即终止，没有下一轮纠错。"""
    registry, executed = test_registry
    model = FakeChatModel([_test_turn(target), _final_turn()])

    result = agent_loop(model, _settings(), _messages(), registry)

    assert result.status == AgentStatus.FAILED
    assert len(model.received_messages) == 1
    assert executed == []


@pytest.mark.parametrize(
    ("limits", "reason"),
    [
        ({"max_steps": 2}, StopReason.MAX_STEPS),
        ({"max_tool_calls": 1}, StopReason.MAX_TOOL_CALLS),
        ({"max_total_tokens": 6}, StopReason.TOKEN_BUDGET),
        ({"input_price_per_million": 1_000_000, "max_cost": 3}, StopReason.COST_BUDGET),
    ],
)
def test_loop_recovery_does_not_reset_budgets(test_registry, limits, reason):
    """测纠错仍受现有预算限制；第二轮达到限制后不执行修正的工具。"""
    registry, executed = test_registry
    model = FakeChatModel([_test_turn(), _test_turn("tests/test_blog.py", "call-fixed"), _final_turn()])

    result = agent_loop(model, _settings(**limits), _messages(), registry)

    assert result.status == AgentStatus.LIMIT_REACHED
    assert result.stop_reason == reason
    assert result.model_steps == 2
    assert result.tool_calls == 1
    assert result.usage.total_tokens == 6
    assert executed == []


def test_loop_checks_timeout_before_recovery_model_request(test_registry):
    """测第一次参数失败后时间耗尽，不再请求模型纠错。"""
    registry, executed = test_registry
    model = FakeChatModel([_test_turn(), _final_turn()])
    # 第一次模型及工具校验均在 0 秒，下一轮循环开始时变为 2 秒。
    times = iter([0, 0, 0, 0, 0, 2])

    result = agent_loop(
        model, _settings(run_timeout_seconds=1), _messages(), registry,
        clock=lambda: next(times, 2),
    )

    assert result.stop_reason == StopReason.RUN_TIMEOUT
    assert len(model.received_messages) == 1
    assert result.tool_calls == 1
    assert executed == []


def test_loop_valid_test_call_needs_no_recovery_turn(test_registry):
    """测一次传对参数时，仍是工具轮→答案轮，不增加模型请求。"""
    registry, executed = test_registry
    model = FakeChatModel([_test_turn("tests/test_blog.py"), _final_turn()])

    result = agent_loop(model, _settings(), _messages(), registry)

    assert result.status == AgentStatus.COMPLETED
    assert result.model_steps == 2
    assert result.tool_calls == 1
    assert executed == ["tests/test_blog.py"]


@pytest.mark.parametrize("repeat_bad_path", [False, True])
def test_loop_search_file_path_errors_share_one_recovery_round(tmp_path, repeat_bad_path):
    """测同轮两次误传文件都回传；下一轮改目录可继续，重复错误则停止。"""
    (tmp_path / "app.py").write_text("needle = 1\n", encoding="utf-8")
    registry = ToolRegistry(tmp_path, [SearchCodeTool()])

    def search_turn(path, call_id):
        return _tool_turn(call_id, "search_code", json.dumps({
            "query": "needle", "path": path, "file_pattern": "*.py",
        }))

    bad_turn = search_turn("app.py", "bad-1")
    bad_turn.tool_calls.extend(search_turn("app.py", "bad-2").tool_calls)
    model = FakeChatModel([
        bad_turn,
        search_turn("app.py" if repeat_bad_path else ".", "next"),
        _final_turn(),
    ])

    result = agent_loop(model, _settings(model="fake"), _messages(), registry, run_dir=tmp_path)

    observations = model.received_messages[1][-2:]
    assert [m["tool_call_id"] for m in observations] == ["bad-1", "bad-2"]
    assert all(json.loads(m["content"])["error"]["code"] == "not_a_directory"
               for m in observations)
    assert result.tool_calls == 3  # 被拒绝的调用也占次数，不重置预算。
    if repeat_bad_path:
        assert result.status == AgentStatus.FAILED
        assert result.stop_reason == StopReason.TOOL_ERROR
        assert result.model_steps == 2
    else:
        assert result.status == AgentStatus.COMPLETED
        assert result.model_steps == 3
        observation = json.loads(model.received_messages[2][-1]["content"])
        assert observation["data"]["matches"][0]["path"] == "app.py"


@pytest.mark.parametrize("search_first", [False, True])
def test_loop_different_error_types_have_independent_recovery(test_registry, tmp_path, search_first):
    """测两种错误先后出现均可纠正；顺序不影响机会，调用/用量照常累计。"""
    registry, executed = test_registry
    (tmp_path / "app.py").write_text("needle = 1\n", encoding="utf-8")
    registry = ToolRegistry(tmp_path, [RunTestTool(), SearchCodeTool()])

    def search_turn(path, call_id):
        return _tool_turn(call_id, "search_code", json.dumps({
            "query": "needle", "path": path, "file_pattern": "*.py",
        }))

    test_turns = [_test_turn(), _test_turn("tests/test_blog.py", "test-fixed")]
    search_turns = [search_turn("app.py", "search-bad"), search_turn(".", "search-fixed")]
    turns = search_turns + test_turns if search_first else test_turns + search_turns
    model = FakeChatModel(turns + [_final_turn()])

    result = agent_loop(model, _settings(), _messages(), registry)

    assert result.status == AgentStatus.COMPLETED
    assert result.model_steps == 5
    assert result.tool_calls == 4
    assert result.usage.total_tokens == 18
    assert executed == ["tests/test_blog.py"]
    errors = [json.loads(m["content"])["error"]["code"] for m in result.messages
              if m["role"] == "tool" and not json.loads(m["content"])["ok"]]
    assert set(errors) == {"unexpected_test_arguments", "not_a_directory"}
