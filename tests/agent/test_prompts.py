"""步骤 1：Agent Prompt 与初始消息的测试。"""

from repopilot.legacy.agent.prompts import build_initial_messages
from repopilot.legacy.config import TASKS_DIR
from repopilot.legacy.task_loader import get_agent_prompt, parse_task


def test_agent_prompt_contains_task_tools_and_final_output_contract():
    """测试 Agent 能看到题面、工具边界和最终答案字段。"""
    task = parse_task(TASKS_DIR / "flaskr-auth-001" / "task.yaml")

    prompt = get_agent_prompt(task)

    assert task["user_request"].strip() in prompt
    assert task["initial_evidence"]["command"] in prompt
    assert "允许工具" in prompt
    assert "search_code" in prompt
    assert "root_cause" in prompt


def test_agent_prompt_does_not_contain_baseline_snapshot_instructions():
    """测试 Agent Prompt 不会误称模型没有工具或将收到完整仓库快照。"""
    task = parse_task(TASKS_DIR / "flaskr-auth-001" / "task.yaml")

    prompt = get_agent_prompt(task)

    assert "这是单次 LLM 基线调用" not in prompt
    assert "你没有文件读取、搜索、测试执行或写入工具" not in prompt
    assert "完整仓库快照将在后文一次性提供" not in prompt


def test_build_initial_messages_creates_system_and_user_messages():
    """测试初始消息按 system、user 的顺序构造。"""
    task = parse_task(TASKS_DIR / "flaskr-auth-001" / "task.yaml")

    messages = build_initial_messages(task, "只读 Agent 系统规则")

    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[0]["content"] == "只读 Agent 系统规则"
    assert task["user_request"].strip() in messages[1]["content"]

