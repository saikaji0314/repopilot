"""步骤 1：Agent 配置加载和资源限制的测试。"""

from dataclasses import replace

import pytest

from repopilot.legacy.config import AgentSettings, load_agent_settings


def _make_settings(tmp_path) -> AgentSettings:
    """构造一个最小的合法配置，供限制校验测试复用。"""
    return AgentSettings(
        workspace_id=None,
        api_key=None,
        provider="test",
        region="test",
        model="fake-model",
        system_prompt="test prompt",
        thinking_enabled=False,
        response_format="json_object",
        repetitions_per_task=1,
        timeout_seconds=30,
        input_price_per_million=1,
        output_price_per_million=1,
        repo_dir=tmp_path,
        max_steps=5,
        max_tool_calls=8,
        max_total_tokens=10_000,
        max_cost=1.0,
        tool_timeout_seconds=10,
        run_timeout_seconds=120,
        max_tool_output_chars=20_000,
    )


def test_agent_config_file_can_be_loaded_without_credentials():
    """测试本地开发时可以从 agent.yaml 读取完整配置而不调用 API。"""
    settings = load_agent_settings(require_credentials=False)

    assert isinstance(settings, AgentSettings)
    assert settings.max_steps > 0
    assert settings.max_tool_calls > 0
    assert settings.max_total_tokens > 0
    assert "工具" in settings.system_prompt


@pytest.mark.parametrize(
    "field_name",
    [
        "max_steps",
        "max_tool_calls",
        "max_total_tokens",
        "max_cost",
        "tool_timeout_seconds",
        "run_timeout_seconds",
        "max_tool_output_chars",
    ],
)
def test_agent_resource_limits_must_be_positive(tmp_path, field_name):
    """测试每个 Agent 专用资源上限都必须大于零。"""
    settings = replace(_make_settings(tmp_path), **{field_name: 0})

    with pytest.raises(ValueError, match=field_name):
        settings.validate(require_credentials=False)

