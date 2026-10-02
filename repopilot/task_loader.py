"""读取 task.yaml，并构造 Agent 可见的单次 LLM Prompt。"""

import json
from pathlib import Path

import yaml


def parse_task(task_file: Path) -> dict:
    task_file = task_file.resolve()
    if task_file.suffix.lower() not in (".yaml", ".yml"):
        raise ValueError(f"不支持的任务文件格式: {task_file.suffix}")
    if not task_file.is_file():
        raise FileNotFoundError(f"任务文件不存在: {task_file}")

    task = yaml.safe_load(task_file.read_text(encoding="utf-8"))
    if not isinstance(task, dict):
        raise ValueError(f"task.yaml 必须是 YAML 对象: {task_file}")

    required_fields = {
        "schema_version",
        "id",
        "title",
        "task_type",
        "repository",
        "user_request",
        "initial_evidence",
        "constraints",
        "required_output",
    }
    missing = sorted(required_fields - task.keys())
    if missing:
        raise ValueError(f"task.yaml 缺少必填字段 {missing}: {task_file}")
    if task["schema_version"] != "1.0":
        raise ValueError(f"不支持的 schema_version: {task['schema_version']}")

    evidence = task["initial_evidence"]
    if not isinstance(evidence, dict):
        raise ValueError("initial_evidence 必须是对象")
    for field in ("command", "summary", "failures"):
        if field not in evidence:
            raise ValueError(f"initial_evidence 缺少字段: {field}")

    constraints = task["constraints"]
    if not isinstance(constraints, dict):
        raise ValueError("constraints 必须是对象")
    for field in ("mode", "max_steps", "timeout_seconds"):
        if field not in constraints:
            raise ValueError(f"constraints 缺少字段: {field}")

    output = task["required_output"]
    if not isinstance(output, dict) or not isinstance(output.get("fields"), dict):
        raise ValueError("required_output.fields 必须是对象")

    task["_task_dir"] = task_file.parent
    return task


def get_task_user_prompt(task: dict) -> str:
    evidence = task["initial_evidence"]
    failures = evidence.get("failures", [])
    failures_text = "\n".join(
        f"- 测试: {item['test']}\n"
        f"  预期: {item['expected']}\n"
        f"  实际: {item['observed']}"
        for item in failures
    ) or "- 无"

    output_fields = json.dumps(
        task["required_output"]["fields"], ensure_ascii=False, indent=2
    )
    forbidden = ", ".join(task["constraints"].get("forbidden_actions", []))

    return (
        "## 本次执行环境\n\n"
        "这是单次 LLM 基线调用。你没有文件读取、搜索、测试执行或写入工具；"
        "不能实际修改仓库。完整仓库快照将在后文一次性提供。\n\n"
        f"## 任务\n\n{task['user_request'].strip()}\n\n"
        "## 初始测试证据\n\n"
        f"测试命令: {evidence['command']}\n"
        f"结果摘要: {evidence['summary']}\n"
        f"失败用例:\n{failures_text}\n\n"
        "## 权限边界\n\n"
        f"模式: {task['constraints']['mode']}\n"
        f"禁止操作: {forbidden}\n"
        "不要声称自己运行了测试、修改了文件或验证了补丁。\n\n"
        "## 输出协议\n\n"
        "只返回一个合法 JSON 对象，不要添加 Markdown 代码块或解释性前后缀。\n"
        "必须且只能包含以下字段，字段含义如下：\n"
        f"{output_fields}\n"
        "evidence 的每一项必须是包含 source 和 observation 的对象。\n"
        "proposed_fix、verification、risks 必须是字符串数组。\n"
    )


def get_agent_prompt(task: dict) -> str:
    evidence = task["initial_evidence"]
    failures = evidence.get("failures", [])
    failures_text = "\n".join(
        f"- 测试: {item['test']}\n"
        f"  预期: {item['expected']}\n"
        f"  实际: {item['observed']}"
        for item in failures
    ) or "- 无"

    output_fields = json.dumps(
        task["required_output"]["fields"], ensure_ascii=False, indent=2
    )
    allowed_tools = ", ".join(task["constraints"].get("allowed_tools", []))
    forbidden = ", ".join(task["constraints"].get("forbidden_actions", []))

    return (
        "## 本次执行环境\n\n"
        "这是一次 agent LLM 调用。你有文件读取、搜索、测试执行工具，但没有写入工具；"
        "不能实际修改仓库。仓库代码可以通过调用工具获取。\n\n"
        f"## 任务\n\n{task['user_request'].strip()}\n\n"
        "## 初始测试证据\n\n"
        f"测试命令: {evidence['command']}\n"
        f"结果摘要: {evidence['summary']}\n"
        f"失败用例:\n{failures_text}\n\n"
        "## 权限边界\n\n"
        f"模式: {task['constraints']['mode']}\n"
        f"允许工具: {allowed_tools}\n"
        f"禁止操作: {forbidden}\n\n"
        "## 输出协议\n\n"
        "调查过程中通过工具调用协议请求工具。"
        "结束调查时，最终答案必须是一个符合以下字段要求的合法 JSON 对象。\n"
        f"{output_fields}\n"
        "evidence 的每一项必须是包含 source 和 observation 的对象。\n"
        "proposed_fix、verification、risks 必须是字符串数组。\n"
    )
