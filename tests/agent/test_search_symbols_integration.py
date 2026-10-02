"""真实索引/工具/Loop 串联；只有模型是 Fake，不读密钥、不联网。"""

import json
from types import SimpleNamespace

from repopilot.agent.loop import FakeChatModel, agent_loop
from repopilot.agent.schemas import AgentStatus, ModelTurn, StopReason, ToolCall, Usage
from repopilot.retrieval.index import build_index
from repopilot.tools.base import SearchSymbolsTool, ToolRegistry


def test_symbols_result_reaches_next_model_request_and_trace(tmp_path):
    """测注册、真实检索、连续调用复用索引、消息配对和落盘结果。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "auth.py").write_text(
        "def login():\n    return 'login-evidence'\n", encoding="utf-8"
    )
    index = build_index(workspace)
    tool = SearchSymbolsTool(index)
    registry = ToolRegistry(workspace, [tool])
    usage = Usage(prompt_tokens=2, completion_tokens=1, total_tokens=3)
    # 连续两次检索：测试工具对象复用同一个预建索引。
    turns = [ModelTurn(
        tool_calls=[ToolCall(
            id=f"symbols-{number}", name="search_symbols",
            arguments_json='{"query":"login","limit":3}',
        )], finish_reason="tool_calls", usage=usage,
    ) for number in (1, 2)]
    turns.append(ModelTurn(content=json.dumps({
        "status": "diagnosed", "summary": "已找到示例函数",
        "root_cause": "这是串联测试，不评估诊断能力",
        "evidence": [{"source": "auth.py:1-2", "observation": "返回固定文本"}],
        "proposed_fix": [], "verification": ["检查检索结果"], "risks": [],
    }, ensure_ascii=False), finish_reason="stop", usage=usage))
    model = FakeChatModel(turns)
    settings = SimpleNamespace(
        model="fake", api_key="fake-only", max_steps=4, max_tool_calls=3,
        max_total_tokens=1000, max_cost=1.0, run_timeout_seconds=30,
        input_price_per_million=1.0, output_price_per_million=1.0,
    )
    run_dir = tmp_path / "run"

    result = agent_loop(
        model, settings, [{"role": "user", "content": "查找 login"}],
        registry, run_dir=run_dir, task_id="symbols-fake",
    )

    assert result.status == AgentStatus.COMPLETED
    assert result.stop_reason == StopReason.FINAL_ANSWER
    assert result.tool_calls == 2
    assert result.model_steps == 3
    assert tool.index is index
    assert model.received_tools == [registry.schema] * 3
    assert registry.schema[0]["function"]["name"] == "search_symbols"
    for number in (1, 2):
        # 后一轮请求必须包含上一轮的真实工具结果，而非只有 Fake 最终答案。
        message = model.received_messages[number][-1]
        assert message["role"] == "tool"
        assert message["tool_call_id"] == f"symbols-{number}"
        observation = json.loads(message["content"])
        assert observation["ok"] is True
        assert observation["tool"] == "search_symbols"
        assert observation["truncated"] is False
        assert "auth.py:1-2" in observation["data"]["context"]
        assert "login-evidence" in observation["data"]["context"]

    events = [json.loads(line) for line in
              (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    started = [e["data"] for e in events if e["event"] == "tool_call_started"]
    finished = [e["data"] for e in events if e["event"] == "tool_call_completed"]
    assert [e["tool_call_id"] for e in started] == ["symbols-1", "symbols-2"]
    assert [e["tool_call_id"] for e in finished] == ["symbols-1", "symbols-2"]
    assert all(e["result"]["ok"] for e in finished)
    assert "login-evidence" in finished[0]["result"]["data"]["context"]
    saved = json.loads((run_dir / "messages.json").read_text(encoding="utf-8"))
    assert saved == result.messages
    assert (run_dir / "final_answer.json").is_file()
