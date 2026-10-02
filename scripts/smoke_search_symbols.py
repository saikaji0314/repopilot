"""单条付费接入冒烟；显式引导新工具，不计入自主检索正式成绩。

运行：python -m scripts.smoke_search_symbols
使用现有配置/预算，只跑 login 一次；不修改正式任务或 system prompt。
"""

import json

from repopilot.agent.bailian import BailianChatModel
from repopilot.agent.loop import agent_loop
from repopilot.agent.prompts import build_initial_messages
from repopilot.agent.schemas import AgentStatus
from repopilot.agent.trace import save_trace
from repopilot.agent_runner import _run_id, task_fields
from repopilot.config import load_agent_settings
from repopilot.retrieval.index import build_index
from repopilot.task_loader import parse_task
from repopilot.tools.base import (
    ListFilesTool, ReadFileTool, RunTestTool, SearchCodeTool,
    SearchSymbolsTool, ToolRegistry,
)
from repopilot.verification import verify_clean_repository, verify_task_defect
from repopilot.workspace import prepared_task_workspace


SMOKE_INSTRUCTION = (
    "本次是 search_symbols 工具接入冒烟测试，不是自主选工具的正式评测。"
    "请先调用 search_symbols，参数 query='login'、limit=3，"
    "根据返回的代码片段继续分析原任务；如片段不足可使用 read_file。"
    "仍遵守原只读权限、证据要求和最终 JSON 对象协议。"
)


def main() -> int:
    settings = load_agent_settings()
    task_dir = settings.tasks_dir / "flaskr-login-002"
    task = parse_task(task_dir / "task.yaml")
    verify_clean_repository(settings.repo_dir, timeout_seconds=settings.timeout_seconds)
    run_dir = settings.runs_dir / "smoke" / _run_id() / task["id"] / "run-01"
    run_dir.mkdir(parents=True, exist_ok=False)
    print(f"SMOKE_RUN_DIR={run_dir}", flush=True)
    save_trace(run_dir / "task.json", {key: task[key] for key in task_fields})

    with prepared_task_workspace(settings.repo_dir, task_dir, task) as workspace:
        verify_task_defect(task, workspace)
        index = build_index(workspace)
        registry = ToolRegistry(workspace, [
            ListFilesTool(), ReadFileTool(), SearchCodeTool(),
            SearchSymbolsTool(index), RunTestTool(),
        ])
        messages = build_initial_messages(task, settings.system_prompt)
        messages[-1]["content"] += "\n\n## 本次冒烟附加要求\n" + SMOKE_INSTRUCTION
        # 保存本次实际工具定义和额外指令，不保存凭据。
        save_trace(run_dir / "tools.json", registry.schema)
        save_trace(run_dir / "smoke.json", {
            "kind": "explicit_tool_smoke", "formal_evaluation": False,
            "instruction": SMOKE_INSTRUCTION, "model": settings.model,
            "thinking_enabled": settings.thinking_enabled,
            "response_format": settings.response_format,
            "max_steps": settings.max_steps, "max_tool_calls": settings.max_tool_calls,
            "max_cost": settings.max_cost, "max_total_tokens": settings.max_total_tokens,
            "run_timeout_seconds": settings.run_timeout_seconds,
            "index_symbols": len(index.symbols), "index_warnings": index.warnings,
        })
        model = BailianChatModel(settings)
        # 冒烟不自动重试网络请求；Loop 自身的有限格式恢复保持原样。
        model.client.max_retries = 0
        try:
            result = agent_loop(model, settings, messages, registry,
                                run_dir=run_dir, task_id=task["id"])
        finally:
            model.client.close()

    observations = [json.loads(m["content"]) for m in result.messages if m["role"] == "tool"]
    symbols_ok = any(o["tool"] == "search_symbols" and o["ok"]
                     and o.get("data", {}).get("context") for o in observations)
    print(f"search_symbols_success={bool(symbols_ok)}; status={result.status}; stop={result.stop_reason}")
    return 0 if symbols_ok and result.status == AgentStatus.COMPLETED else 1


if __name__ == "__main__":
    raise SystemExit(main())
