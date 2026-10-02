from repopilot.config import TASKS_DIR
from repopilot.task_loader import get_task_user_prompt, parse_task


def test_prompt_contains_output_contract_and_no_tool_boundary():
    task = parse_task(TASKS_DIR / "flaskr-auth-001" / "task.yaml")

    prompt = get_task_user_prompt(task)

    assert "这是单次 LLM 基线调用" in prompt
    assert "你没有文件读取、搜索、测试执行或写入工具" in prompt
    assert "只返回一个合法 JSON 对象" in prompt
    for field in (
        "status",
        "summary",
        "root_cause",
        "evidence",
        "proposed_fix",
        "verification",
        "risks",
    ):
        assert field in prompt


def test_optional_workspace_additions_is_not_required():
    task = parse_task(TASKS_DIR / "flaskr-login-002" / "task.yaml")

    assert task.get("workspace_additions", []) == []
