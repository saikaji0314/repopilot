"""步骤 1：Agent 状态和协议模型的测试。"""

import pytest
from pydantic import ValidationError

from repopilot.agent.schemas import (
    AgentRunStatus,
    AgentStatus,
    ModelTurn,
    ToolCall,
    ToolError,
    ToolResult,
    Usage,
)


def test_agent_status_follows_the_allowed_path():
    """测试 CREATED 可以进入 RUNNING，再进入终态 COMPLETED。"""
    state = AgentRunStatus()

    assert state.status == AgentStatus.CREATED
    state.transition_to(AgentStatus.RUNNING)
    state.transition_to(AgentStatus.COMPLETED)

    assert state.status == AgentStatus.COMPLETED


def test_terminal_status_cannot_return_to_running():
    """测试任务结束后不能重新进入 RUNNING。"""
    state = AgentRunStatus()
    state.transition_to(AgentStatus.RUNNING)
    state.transition_to(AgentStatus.COMPLETED)

    with pytest.raises(ValueError):
        state.transition_to(AgentStatus.RUNNING)

    assert state.status == AgentStatus.COMPLETED


def test_status_cannot_be_assigned_directly():
    """测试调用方不能绕过 transition_to 直接覆盖状态。"""
    state = AgentRunStatus()

    with pytest.raises(AttributeError):
        state.status = AgentStatus.COMPLETED


@pytest.mark.parametrize(
    ("ok", "error"),
    [
        (True, ToolError(code="unexpected", message="不应该出现的错误")),
        (False, None),
    ],
)
def test_tool_result_rejects_conflicting_success_and_error(ok, error):
    """测试成功结果不能带 error，失败结果必须带 error。"""
    with pytest.raises(ValidationError):
        ToolResult(ok=ok, tool="read_file", error=error)


def test_tool_result_rejects_unknown_fields():
    """测试工具结果不能悄悄接受协议外字段。"""
    with pytest.raises(ValidationError):
        ToolResult(ok=True, tool="read_file", unknown="value")


def test_usage_rejects_negative_token_count():
    """测试 Token 用量不能是负数。"""
    with pytest.raises(ValidationError):
        Usage(total_tokens=-1)


def test_model_turn_can_hold_tool_calls_and_text_together():
    """测试模型一轮响应可以同时包含提示文字和结构化工具调用。"""
    turn = ModelTurn(
        content="我先搜索相关代码。",
        tool_calls=[
            ToolCall(
                id="call-1",
                name="search_code",
                arguments_json='{"query":"get_post","path":"."}',
            )
        ],
        finish_reason="tool_calls",
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )

    assert turn.content == "我先搜索相关代码。"
    assert turn.tool_calls[0].name == "search_code"

