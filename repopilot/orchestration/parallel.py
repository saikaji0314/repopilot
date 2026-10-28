"""并行子任务工作区、独立提交与串行整合；不直接管理阶段状态。"""

from pathlib import Path
from uuid import uuid4

from repopilot.agents import loop as engine
from repopilot.agents.prompts import INTEGRATION_PROMPT, SYSTEM_PROMPT
from repopilot.agents.reports import ImplementationReport, IntegrationReport
from repopilot.execution.sandbox import repository_session
from repopilot.execution.workspace import Workspace, run_git
from repopilot.storage.jobs import timestamp


def create_worktree(task, store_root, run_id, subtask_id, base_commit):
    """每个并行子任务独立worktree；路径保留供证据回查。"""
    root = Path(store_root) / "worktrees"
    path = root / f"{run_id}-{subtask_id}-{uuid4().hex[:8]}"
    workspace = Workspace(task.repository, root, path=path, base_commit=base_commit)
    workspace.prepare()
    return workspace


def run_subtask(*, task, settings, control, trace, workspace, payload, model, edits, material_tools):
    """在一个独立worktree执行子任务；只返回报告和提交，不修改父状态。"""
    root = workspace.repository()
    started_at = timestamp()
    with repository_session(task, root, writable=True, deadline=control.remaining,
                            trace=trace) as session:
        tools = engine.build_tools(task, control.remaining, workspace=root, edits=edits,
            **({} if session is None else {"repository": session, "checker": session.run_check}))
        tools += material_tools
        report = engine.run_phase(prompt=SYSTEM_PROMPT, schema=ImplementationReport,
            payload=payload, tools=tools, settings=settings, control=control,
            model=model, phase="implement")
    dirty = bool(run_git(root, "status", "--porcelain=v1", "--untracked-files=all").stdout.strip())
    report = engine.consistent_status(report, workspace.changes() if dirty else {})
    trace.save(f"parallel-report-{workspace.path.name}.json", report.model_dump(mode="json"))
    commit = None
    if report.status == "implemented" and dirty:
        commit = workspace.commit(f"chore: 并行子任务 {payload.get('subtask', {}).get('id', '')}")
    return {"report": report.model_dump(mode="json"), "commit": None if commit is None else commit["commit"],
            "worktree": str(root), "base_commit": workspace.base_commit, "dirty": dirty,
            "started_at": started_at, "finished_at": timestamp()}


def resolve_conflict(*, task, settings, control, trace, workspace, payload, model, edits,
                     material_tools):
    """在父整合工作区用受控实现Agent处理普通合并冲突。"""
    worker = control.fork("integrate")
    worker_trace = trace.fork()
    with repository_session(task, workspace.repository(), writable=True,
                            deadline=worker.remaining, trace=worker_trace) as session:
        tools = engine.build_tools(task, worker.remaining, workspace=workspace.repository(),
                                   edits=edits,
                                   **({} if session is None else {"repository": session,
                                                                  "checker": session.run_check}))
        tools += material_tools
        report = engine.run_phase(prompt=INTEGRATION_PROMPT, schema=IntegrationReport,
            payload=payload, tools=tools, settings=settings, control=worker,
            model=model, phase="integrate")
    worker_trace.save("integration-report.json", report.model_dump(mode="json"))
    return report.model_dump(mode="json")


def apply_integration(parent, source_commit):
    """把子任务提交应用到父工作区但不提交；返回冲突文件。"""
    result = parent.apply_commit(source_commit)
    conflicts = parent.integration_conflicts()
    return result.returncode == 0 and not conflicts, conflicts, result
