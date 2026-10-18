from pathlib import Path
import uuid
import argparse
import time
import hashlib
from copy import deepcopy
from datetime import datetime, timezone
from repopilot.legacy.agent.bailian import BailianChatModel
from repopilot.legacy.config import load_agent_settings, AgentSettings
from repopilot.legacy.agent.loop import agent_loop
from repopilot.legacy.verification import verify_clean_repository, verify_task_defect
from repopilot.legacy.workspace import prepared_task_workspace
from repopilot.legacy.task_loader import parse_task
from repopilot.legacy.tools.base import ToolRegistry, ListFilesTool, ReadFileTool, SearchCodeTool, SearchSymbolsTool, RunTestTool
from repopilot.legacy.agent.prompts import build_initial_messages
from repopilot.legacy.agent.schemas import AgentStatus
from repopilot.legacy.agent.trace import save_trace, TraceFilename
from repopilot.legacy.retrieval.index import build_index
from repopilot.legacy.repository_runtime import resolve_repository


task_fields = {
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


def _run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _select_task_dirs(settings: AgentSettings, task_ids: list[str] | None) -> list[Path]:
    all_dirs = sorted(path for path in settings.tasks_dir.iterdir() if path.is_dir())
    by_id = {path.name: path for path in all_dirs}
    if task_ids is None:
        return all_dirs

    unknown = sorted(set(task_ids) - by_id.keys())
    if unknown:
        raise ValueError(f"未知任务 ID: {unknown}")
    return [by_id[task_id] for task_id in task_ids]


def build_tools(workspace: Path, python: Path | None, retrieval_mode: str):
    """关闭符号检索时不建索引，也不提示使用不存在的工具。"""
    search_tool = SearchCodeTool()
    tools = [ListFilesTool(), ReadFileTool(), search_tool]
    index_seconds = 0.0
    if retrieval_mode == "symbols":
        start = time.monotonic()
        tools.append(SearchSymbolsTool(build_index(workspace)))
        index_seconds = time.monotonic() - start
    elif retrieval_mode == "code":
        search_tool.description = search_tool.description.replace(
            "定位 Python 函数或类的定义且已知名称时，优先使用 search_symbols；", ""
        )
    else:
        raise ValueError("retrieval_mode 必须为 code 或 symbols")
    tools.append(RunTestTool(python))
    return ToolRegistry(workspace, tools), index_seconds


def run_agent(*, task_ids: list[str] | None = None, repetitions: int | None = None,
              retrieval_mode: str = "symbols", output_dir: Path | None = None) -> int:
    # 加载配置
    settings = load_agent_settings()
    if retrieval_mode not in {"code", "symbols"}:
        raise ValueError("retrieval_mode 必须为 code 或 symbols")

    # 选择任务
    selected_dirs = _select_task_dirs(settings, task_ids)
    repeat_count = repetitions if repetitions is not None else settings.repetitions_per_task
    if repeat_count < 1:
        raise ValueError("repetitions 必须大于等于 1")

    # 先解析并验证本次涉及的每个仓库，不能把所有任务都套到默认 Flaskr。
    tasks = []
    checked_repositories = set()
    try:
        for task_dir in selected_dirs:
            task = parse_task(task_dir / "task.yaml")
            if task["id"] != task_dir.name:
                raise ValueError("任务目录名与 task.yaml.id 不一致")
            repo, python = resolve_repository(task, settings.repo_dir)
            tasks.append((task_dir, task, repo, python))
            if (repo, python) not in checked_repositories:
                clean_result = verify_clean_repository(
                    repo, timeout_seconds=settings.timeout_seconds, python_executable=python
                )
                print(f"干净基线 {repo.name}: {clean_result.stdout.strip().splitlines()[-1]}")
                checked_repositories.add((repo, python))
    except RuntimeError as e:
        print(f"干净基线测试失败: {e}")
        return 1

    # 组装 Agent
    model = BailianChatModel(settings)

    # 创建缺陷工作区
    batch_id = _run_id()

    results = []
    for task_dir, task, repo, python in tasks:
        print(f"\n执行任务: {task_dir.name}")

        for run_number in range(1, repeat_count + 1):
            root = output_dir if output_dir is not None else settings.runs_dir / "agent" / batch_id
            run_dir = root / task["id"] / f"run-{run_number:02d}"
            run_dir.mkdir(parents=True, exist_ok=False)

            save_trace(run_dir / TraceFilename.TASK, {field: task[field] for field in task_fields})

            with prepared_task_workspace(repo, task_dir, task) as workspace:
                try:
                    defect_result = verify_task_defect(task, workspace, python_executable=python)
                except RuntimeError as e:
                    print(f"缺陷测试失败: {e}")
                    return 1
                print(f"缺陷测试通过: {defect_result.stdout.strip().splitlines()[-1]}")
                tool_registry, index_seconds = build_tools(workspace, python, retrieval_mode)
                visible_task = deepcopy(task)
                visible_task["constraints"]["allowed_tools"] = [t.name for t in tool_registry.tools]
                # 构建初始消息  
                initial_messages = build_initial_messages(visible_task, settings.system_prompt)
                # 只保存非敏感配置；凭据及工作区账号标识不进入快照。
                safe_settings = {k: str(v) if isinstance(v, Path) else v
                                 for k, v in vars(settings).items()
                                 if k not in {"api_key", "workspace_id"}}
                task_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in task_dir.iterdir() if p.is_file()}
                save_trace(run_dir / "runtime.json", {
                    "retrieval_mode": retrieval_mode, "settings": safe_settings,
                    "tools": tool_registry.schema, "initial_messages": initial_messages,
                    "index_seconds": index_seconds, "task_hashes": task_hashes,
                    "test_python": str(python) if python else None,
                })
                save_trace(run_dir / TraceFilename.TASK, {field: visible_task[field] for field in task_fields})
                # 运行并打印简短结论                
                if task["id"] != task_dir.name:
                    raise ValueError(f"任务目录名与 task.yaml.id 不一致: {task_dir.name} != {task['id']}")
                try:
                    result = agent_loop(model, settings, 
                        initial_messages=initial_messages, 
                        tool_registry=tool_registry,
                        run_dir=run_dir,
                        task_id=task["id"])
                except Exception as e:
                    print(f"运行任务 {task_dir.name} 时出错: {e}")
                    return 1
                results.append(result)

        print(f"任务 {task_dir.name} 完成")

    # 根据终态设置进程退出码
    exit_code = 0 if all(result.status == AgentStatus.COMPLETED for result in results) else 1
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行 RepoPilot Agent")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true", help="运行全部任务")
    selection.add_argument(
        "--task",
        action="append",
        dest="task_ids",
        metavar="TASK_ID",
        help="只运行指定任务；可重复提供",
    )
    parser.add_argument(
        "--retrieval-mode", choices=["code", "symbols"], default="symbols",
        help="code 为原四工具，symbols 为加入符号检索的五工具",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        help="覆盖配置中的每任务调用次数",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    exit_code = run_agent(
        task_ids=None if args.all else args.task_ids,
        repetitions=args.repetitions,
        retrieval_mode=args.retrieval_mode,
    )
    raise SystemExit(exit_code)
