"""任务生命周期入口：创建、恢复、重试、审批和结果查询。"""

import json
from pathlib import Path
from uuid import uuid4

from repopilot.agents import loop as engine
from repopilot.storage.budget import Budget
from repopilot.core.config import RuntimeSettings, Task, execution_options
from repopilot.orchestration.session import execute_run
from repopilot.orchestration.state import TERMINAL, approval_digest, attach, canonical_hash
from repopilot.storage.jobs import JobStore, StateError, job_lock, timestamp
from repopilot.execution.workspace import head_commit, source_snapshot


def start(task, settings, output_root, *, model=None, workspace=True, review=True,
          require_approval=True, stop_after=None, issue=None, retry_of=None, retry_reason=None, base_commit=None):
    state = create_job(task, settings, output_root, workspace=workspace, review=review,
        require_approval=require_approval, issue=issue, retry_of=retry_of, retry_reason=retry_reason, base_commit=base_commit)
    return resume(state["run_id"], settings, output_root, model=model, stop_after=stop_after)


def create_job(task, settings, output_root, *, workspace=True, review=True,
               require_approval=True, issue=None, retry_of=None, retry_reason=None, base_commit=None):
    """先持久化任务身份，允许调度器在执行模型之前记录队列对应关系。"""
    store = JobStore(output_root)
    state = store.create({
        "run_id": f"{task.id}-{uuid4().hex[:12]}", "task": task.model_dump(mode="json"),
        "settings": settings.model_dump(mode="json", exclude={"api_key"}),
        "workspace_enabled": workspace, "review_enabled": review,
        "require_approval": require_approval and workspace and review,
        "status": "ready", "phase": "prepare", "in_flight": None,
        "base_commit": base_commit, "before": None, "expected_head": None,
        "report": None, "review_report": None, "history": [], "verified": [],
        "reviewed_commit": None, "reworks": 0, "rework_pending": False,
        "control": {}, "edits": {}, "active_seconds": 0, "issue": issue,
        "approval": None, "created_at": timestamp(), "retry_of": retry_of, "retry_reason": retry_reason,
    }, issue_key=None if issue is None else f"{issue['repository']}#{issue['number']}", retry_of=retry_of)
    if issue and canonical_hash(issue) != canonical_hash(state["issue"]):
        raise StateError("issue_changed_since_intake")
    return state


def frozen_settings(saved, current, overrides=None):
    """恢复旧模型配置；只从匹配供应商端点取得当前凭证，允许同端点轮换密钥。"""
    if (saved.get("provider", "deepseek"), saved["base_url"]) != (current.provider, current.base_url):
        raise StateError("model_credentials_endpoint_mismatch")
    return RuntimeSettings.model_validate({**saved, **execution_options(overrides or {}),
                                          "api_key": current.api_key})


def retry(run_id, settings, root, *, reason, overrides=None, model=None, stop_after=None, base_commit=None):
    """明确开始新尝试，保留失败运行及同一累计预算；不自动循环重试。"""
    store = JobStore(root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        if state["status"] not in {"limit_reached", "failed", "phase_error", "workspace_error",
                                   "review_inconclusive", "rework_limit_reached", "rejected"}:
            raise StateError("retry_requires_finished_attempt")
        if not reason.strip():
            raise StateError("reason_required")
        frozen = frozen_settings(state["settings"], settings, overrides)
        return start(Task.model_validate(state["task"]), frozen, root, model=model,
            workspace=state["workspace_enabled"], review=state["review_enabled"],
            require_approval=state["require_approval"], issue=state["issue"], stop_after=stop_after,
            retry_of=run_id, retry_reason=reason, base_commit=base_commit)


def resume(run_id, settings, output_root, *, model=None, stop_after=None):
    store = JobStore(output_root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        settings = frozen_settings(state["settings"], settings)
        if state["in_flight"] == "publish":
            return snapshot(store, state, settings)
        if state["in_flight"]:
            state["status"] = "recovery_required"
            state["stop_reason"] = "interrupted_phase_requires_explicit_recovery"
            store.save(state)
            return snapshot(store, state, settings)
        if state["status"] in TERMINAL or state["status"] in {"awaiting_approval", "approved"}:
            return snapshot(store, state, settings)
        return drive(store, state, settings, model=model, stop_after=stop_after)


def drive(store, state, settings, *, model=None, stop_after=None):
    execute_run(store, state, settings, model=model, stop_after=stop_after)
    return snapshot(store, state, settings)


def decide(run_id, root, *, commit, approved, reason):
    store = JobStore(root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        if state["status"] != "awaiting_approval":
            raise StateError("not_waiting_for_approval")
        if commit != state["reviewed_commit"] or approval_digest(state) != state["approval_digest"]:
            raise StateError("stale_approval")
        attach(state, store.root).assert_clean(commit)
        state["approval"] = {"commit": commit, "digest": state["approval_digest"], "approved": approved,
                             "reason": reason, "at": timestamp()}
        state["status"], state["stop_reason"] = ("approved", "human_approved") if approved else ("rejected", "human_rejected")
        store.save(state)
        return {"run_id": run_id, "status": state["status"], "approval": state["approval"]}


def recover(run_id, root, *, commit, reason):
    """中途断开的阶段不自动重放；操作者核对现场后才允许从当前文件继续。"""
    store = JobStore(root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        if not state["in_flight"]:
            raise StateError("no_interrupted_phase")
        if state["in_flight"] == "publish":
            raise StateError("use_publish_to_reconcile_remote_effects")
        if state["phase"] == "prepare":
            source = Path(state["task"]["repository"])
            if commit != (state["base_commit"] or head_commit(source)):
                raise StateError("recovery_commit_mismatch")
            if state["before"] and state["before"] != source_snapshot(source):
                raise StateError("source_changed_during_prepare")
            state["base_commit"], state["before"] = commit, source_snapshot(source)
            attach(state, store.root).prepare()
        workspace = attach(state, store.root)
        if workspace is None or head_commit(workspace.repository()) != commit:
            raise StateError("recovery_commit_mismatch")
        state["recovery"] = {"commit": commit, "reason": reason, "phase": state["phase"], "at": timestamp()}
        state["in_flight"] = None
        state["status"], state["stop_reason"] = "paused", "operator_recovery"
        # 明确接纳中间改动；只有实现阶段允许从未提交的现场继续。
        if state["phase"] in {"implement", "rework", "anchor"}:
            state["expected_head"] = None
        state["workspace_snapshot"] = source_snapshot(workspace.repository())
        store.save(state)
        return {"run_id": run_id, "status": "paused", "recovery": state["recovery"]}


def snapshot(store, state, settings):
    directory = store.root / "runs" / state["run_id"]
    directory.mkdir(parents=True, exist_ok=True)
    trace = engine.Trace(directory, settings.api_key.get_secret_value())
    workspace = attach(state, store.root)
    changes = None
    if workspace and workspace.path.exists():
        changes = workspace.changes() | workspace.diff()
        trace.save("changes.json", changes)
    report, review = state["report"], state["review_report"]
    budget = Budget(store.root / "budget.sqlite3", settings.budget_cny)
    summary = {
        "run_id": state["run_id"], "status": state["status"], "stop_reason": state.get("stop_reason"),
        "phase": state["phase"], "in_flight": state["in_flight"], "task_sha256": canonical_hash(state["task"]),
        "model": settings.model, **{k: state["control"].get(k, 0) for k in
            ("model_calls", "tool_calls", "input_tokens", "output_tokens")},
        "checks": state["control"].get("checks", []), "cost": budget.snapshot(state["run_id"]), "budget": budget.snapshot(),
        "report_status": None if report is None else report["status"], "edits": state["edits"],
        "output_directory": str(directory), "issue": state["issue"], "approval": state["approval"],
        "changes": None if changes is None else {k: changes[k] for k in
            ("changed_files", "additions", "deletions", "base_commit", "head_commit", "worktree")},
        "repository_unchanged": None if state["before"] is None else state["before"] == source_snapshot(Path(state["task"]["repository"])),
        "review": {"enabled": state["review_enabled"] and state["workspace_enabled"],
            "review_rounds": len(state["history"]), "rework_attempts": state["reworks"],
            "reviewed_commit": state["reviewed_commit"], "verdict": None if review is None else review["verdict"],
            "findings": 0 if review is None else len(review["findings"]), "limit": settings.max_rework,
            "final_commit": None if changes is None else changes["head_commit"]},
    }
    trace.save("summary.json", summary)
    return json.loads(trace.encode(summary))
