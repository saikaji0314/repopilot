"""步骤 7：假 SDK 客户端测试；不会创建真实连接，也不读取 .env。"""

from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from repopilot.legacy.agent import bailian
from repopilot.legacy.agent.schemas import ModelTurn


def response(content="最终答案", calls=None):
    """模拟 SDK 的属性结构：choices[0].message 与顶层 usage。"""
    return NS(choices=[NS(message=NS(content=content, tool_calls=calls),
                          finish_reason="tool_calls" if calls else "stop")],
              usage=NS(prompt_tokens=10, completion_tokens=3, total_tokens=13))


@pytest.fixture
def sdk(monkeypatch):
    state = NS(init=None, requests=[], response=response(), error=None)

    def create(**kwargs):
        state.requests.append(deepcopy(kwargs))
        if state.error:
            raise state.error
        return state.response

    def fake_openai(**kwargs):
        state.init = kwargs
        return NS(chat=NS(completions=NS(create=create)))

    monkeypatch.setattr(bailian, "OpenAI", fake_openai)
    return state


@pytest.fixture
def settings():
    return NS(api_key="fake-key", base_url="https://example.invalid/v1",
              timeout_seconds=30, model="fake-qwen", thinking_enabled=False,
              response_format="json_object")


def test_client_uses_configured_timeout_seconds(sdk, settings):
    """测 AgentSettings 的 timeout_seconds 被传给客户端。"""
    bailian.BailianChatModel(settings)
    assert sdk.init["timeout"] == 30
    assert sdk.init["api_key"] == "fake-key"
    assert sdk.init["base_url"] == settings.base_url


def test_every_request_keeps_messages_tools_and_thinking_setting(sdk, settings):
    """测每轮都传工具和完整消息，不再拼装基线 Prompt；禁用流式。"""
    model = bailian.BailianChatModel(settings)
    messages = [{"role": "system", "content": "规则"}, {"role": "user", "content": "调查"}]
    tools = [{"type": "function", "function": {"name": "list_files"}}]
    model.complete(messages, tools)
    model.complete(messages, tools)
    assert len(sdk.requests) == 2
    for request in sdk.requests:
        assert request["messages"] == messages
        assert request["tools"] == tools
        assert request["model"] == settings.model
        assert request.get("stream", False) is False
        assert request["extra_body"]["enable_thinking"] is False
        assert request["response_format"] == {"type": "json_object"}
        assert "tool_choice" not in request


def test_final_answer_recovery_disables_tool_choice_only_for_that_request(sdk, settings):
    """测纠错轮明确传 none；普通调查轮仍由模型自主选择工具。"""
    model = bailian.BailianChatModel(settings)
    model.complete([], [])
    model.complete([], [], tool_choice="none")

    assert "tool_choice" not in sdk.requests[0]
    assert sdk.requests[1]["tool_choice"] == "none"
    assert sdk.requests[1]["response_format"] == {"type": "json_object"}


def test_text_response_becomes_model_turn(sdk, settings):
    """测 SDK 响应逐字段转换，不能把整个 response 当作 ModelTurn。"""
    turn = bailian.BailianChatModel(settings).complete([], [])
    assert isinstance(turn, ModelTurn)
    assert turn.content == "最终答案"
    assert turn.tool_calls == []
    assert turn.finish_reason == "stop"
    assert turn.usage.model_dump() == dict(prompt_tokens=10, completion_tokens=3, total_tokens=13)


def test_multiple_tool_calls_preserve_ids_and_raw_arguments(sdk, settings):
    """测 content=None 和多工具；坏 JSON 参数留给注册表处理。"""
    sdk.response = response(None, [
        NS(id="call-1", type="function", function=NS(name="list_files", arguments='{"path":"."}')),
        NS(id="call-2", type="function", function=NS(name="read_file", arguments="{bad json")),
    ])
    turn = bailian.BailianChatModel(settings).complete([], [])
    assert turn.content is None
    assert turn.finish_reason == "tool_calls"
    assert [(c.id, c.name, c.arguments_json) for c in turn.tool_calls] == [
        ("call-1", "list_files", '{"path":"."}'), ("call-2", "read_file", "{bad json"),
    ]


@pytest.mark.parametrize("missing", ["choices", "usage"])
def test_incomplete_response_is_explicit_error(sdk, settings, missing):
    """测空 choices 或缺 usage 明确报错；本项目不把未知用量当零成本。"""
    setattr(sdk.response, missing, [] if missing == "choices" else None)
    with pytest.raises(ValueError):
        bailian.BailianChatModel(settings).complete([], [])


def test_sdk_exception_reaches_loop(sdk, settings):
    """测请求异常交给 Loop 记录 MODEL_ERROR，不伪造成正常答案。"""
    sdk.error = RuntimeError("simulated API failure")
    with pytest.raises(RuntimeError, match="simulated API failure"):
        bailian.BailianChatModel(settings).complete([], [])
