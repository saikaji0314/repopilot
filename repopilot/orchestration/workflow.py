"""任务生命周期入口：创建、恢复、重试、审批和结果查询。"""

import json
from pathlib import Path
from uuid import uuid4

from repopilot.agents.trace import Trace
from repopilot.storage.budget import Budget
from repopilot.core.config import RuntimeSettings, Task, execution_options
from repopilot.delivery.forge import ForgeError, issue_source_key, parse_issue_url
from repopilot.orchestration.session import execute_run
from repopilot.orchestration import human
from repopilot.orchestration import knowledge
from repopilot.orchestration.slots import SlotManager
from repopilot.knowledge.documents import KnowledgeFault
from repopilot.collaboration.requests import resolve_request
from repopilot.orchestration.state import TERMINAL, approval_digest, attach, canonical_hash
from repopilot.planning import brief as task_brief
from repopilot.planning import planner as planning
from repopilot.review import basis as review_basis_store
from repopilot.review.merge import approval_problems
from repopilot.storage.jobs import JobStore, StateError, job_lock, timestamp
from repopilot.execution.workspace import head_commit, source_snapshot


def start(task, settings, output_root, *, model=None, workspace=True, review=True,
          require_approval=True, stop_after=None, issue=None, retry_of=None, retry_reason=None,
          base_commit=None, human_answers=None, carry=None):
    state = create_job(task, settings, output_root, workspace=workspace, review=review,
        require_approval=require_approval, issue=issue, retry_of=retry_of, retry_reason=retry_reason,
        base_commit=base_commit, human_answers=human_answers, carry=carry)
    return resume(state["run_id"], settings, output_root, model=model, stop_after=stop_after)


def create_job(task, settings, output_root, *, workspace=True, review=True,
               require_approval=True, issue=None, retry_of=None, retry_reason=None,
               base_commit=None, human_answers=None, carry=None):
    """先持久化任务身份，允许调度器在执行模型之前记录队列对应关系。"""
    # 只读调查没有worktree，不能进入需要Git基线的规划；静默关闭而不是修改调用方设置。
    if not workspace and settings.plan_enabled:
        settings = settings.model_copy(update={"plan_enabled": False})
    carried = json.loads(json.dumps(carry or {}))
    prior_plan = carried.get("plan") if settings.plan_enabled else None
    initial_brief = carried.get("task_brief") or task_brief.initial(task)
    brief_history = carried.get("task_brief_history") or {
        initial_brief["sha256"]: initial_brief}
    store = JobStore(output_root)
    if issue:
        source = parse_issue_url(issue['url'])
        if (source['repository'].lower() != issue['repository'].lower()
                or source['number'] != issue['number']):
            raise StateError('issue_identity_mismatch')
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
        "human_requests": [], "human_answers": list(carried.get("human_answers") or human_answers or []),
        "requirements_revision": len(carried.get("human_answers") or human_answers or []),
        "question_batches": list(carried.get("question_batches") or []),
        "question_handovers": list(carried.get("question_handovers") or []),
        "question_limit": carried.get("question_limit"),
        "task_brief": initial_brief, "task_brief_history": brief_history,
        "plan": prior_plan, "plan_history": list(carried.get("plan_history") or []) if prior_plan else [],
        "plan_progress": ({"order": prior_plan["order"], "current": None, "results": [],
                           "started_at": timestamp(), "reused": True} if prior_plan else
                          {"order": [], "current": None, "results": []}),
        "plan_refresh_history": list(carried.get("plan_refresh_history") or []) if prior_plan else [],
        "plan_refresh_required": bool(carried.get("plan_refresh_required")) if prior_plan else False,
        "subtask_mappings": list(carried.get("subtask_mappings") or []) if prior_plan else [],
        "parallel_runs": [], "integrations": [], "parallel_blocked": [], "pending_integration": None,
        "platform_preflight": None,
        "review_rounds": [], "review_basis": None, "review_reuse": [],
        "review_invalidations": [], "review_pending": None, "review_draft": None,
        "disputes": [], "review_dispute_decisions": [], "review_schema": 2,
        "rework_limit": settings.max_rework, "rework_extensions": [],
        "prior_attempt": carried.get("prior_attempt"),
    }, issue_key=None if issue is None else issue_source_key(issue['url']), retry_of=retry_of,
        legacy_issue_key=None if issue is None else f"{issue['repository']}#{issue['number']}")
    if issue and canonical_hash(issue) != canonical_hash(state["issue"]):
        raise StateError("issue_changed_since_intake")
    return state


def frozen_settings(saved, current, overrides=None):
    """恢复旧模型配置；只从匹配供应商端点取得当前凭证，允许同端点轮换密钥。

    旧运行没有M3开关字段时按关闭处理，避免升级后自动多一次规划或外部评论；
    显式override（例如retry --plan）仍可开启。
    """
    if (saved.get("provider", "deepseek"), saved["base_url"]) != (current.provider, current.base_url):
        raise StateError("model_credentials_endpoint_mismatch")
    overrides = execution_options(overrides or {})
    merged = {**saved, **overrides}
    for key in ("plan_enabled", "issue_comments_enabled"):
        if key not in saved and key not in overrides:
            merged[key] = False
    return RuntimeSettings.model_validate({**merged, "api_key": current.api_key})


def review_limit(state, settings):
    """M5 运行可经人工决定延长修改上限；旧运行回落到冻结配置。"""

    stored = state.get("rework_limit")
    return int(stored) if stored is not None else settings.max_rework


def retry(run_id, settings, root, *, reason, overrides=None, model=None, stop_after=None, base_commit=None):
    """明确开始新尝试，保留失败运行及同一累计预算；不自动循环重试。"""
    store = JobStore(root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        retryable = state["status"] in {"limit_reached", "failed", "phase_error", "workspace_error",
                                        "review_inconclusive", "rework_limit_reached", "rejected"}
        # 计划中某个子任务报告blocked时保留completed原语义；它是可人工交接的阻塞，不是正常完成。
        if state["status"] == "completed" and str(state.get("stop_reason") or "").startswith("subtask_"):
            retryable = True
        if not retryable:
            raise StateError("retry_requires_finished_attempt")
        if not reason.strip():
            raise StateError("reason_required")
        frozen = frozen_settings(state["settings"], settings, overrides)
        carry = {
            "human_answers": state.get("human_answers"),
            "question_batches": state.get("question_batches"),
            "question_handovers": state.get("question_handovers"),
            "question_limit": state.get("question_limit"),
            "task_brief": state.get("task_brief"),
            "task_brief_history": state.get("task_brief_history"),
            "plan": state.get("plan"),
            "plan_history": state.get("plan_history"),
            "plan_refresh_history": state.get("plan_refresh_history"),
            "plan_refresh_required": state.get("plan_refresh_required"),
            "subtask_mappings": state.get("subtask_mappings"),
            # 旧尝试的计数只作交接背景；新尝试的修改上限仍由本次配置决定，不能静默重置。
            "prior_attempt": {
                "run_id": state["run_id"], "status": state.get("status"),
                "reworks": state.get("reworks", 0),
                "review_rounds": len(state.get("history") or []),
                "review_verdict": (state.get("review_report") or {}).get("verdict"),
                "disputes": len(state.get("disputes") or []),
            },
        }
        return start(Task.model_validate(state["task"]), frozen, root, model=model,
            workspace=state["workspace_enabled"], review=state["review_enabled"],
            require_approval=state["require_approval"], issue=state["issue"], stop_after=stop_after,
            retry_of=run_id, retry_reason=reason, base_commit=base_commit, carry=carry)


def resume(run_id, settings, output_root, *, model=None, stop_after=None):
    store = JobStore(output_root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        review_basis_store.ensure(state)
        settings = frozen_settings(state["settings"], settings)
        if state["in_flight"] == "publish":
            result = snapshot(store, state, settings)
        elif state["in_flight"]:
            state["status"] = "recovery_required"
            state["stop_reason"] = "interrupted_phase_requires_explicit_recovery"
            human.recovery_request(state, store.root)
            store.save(state)
            result = snapshot(store, state, settings)
        else:
            if state["status"] in {"ready", "paused", "needs_input", "awaiting_approval", "approved"}:
                try:
                    if state.get("base_commit") and knowledge.sync_answers(
                            state, store.root, (settings.api_key.get_secret_value(),)):
                        # 文档索引和任务库之间没有跨库事务；只重放已有人工事件的派生产物。
                        store.save(state)
                    knowledge.validate(state, store.root)
                except KnowledgeFault as exc:
                    knowledge.pause(state, store.root, str(exc))
                    store.save(state)
            if state["status"] in TERMINAL or state["status"] in {"awaiting_approval", "approved"}:
                result = snapshot(store, state, settings)
            else:
                result = drive(store, state, settings, model=model, stop_after=stop_after)
    return _auto_deliver_comment(store, run_id, settings, result)


def _auto_deliver_comment(store, run_id, settings, result):
    """Issue任务等待澄清时先落盘再自动发批次评论；失败只记录，不吞掉等待状态。"""
    if not settings.issue_comments_enabled:
        return result
    state = store.get(run_id)
    # 旧运行没有显式开关时不自动产生外部评论；仍可显式 human post。
    if "issue_comments_enabled" not in (state.get("settings") or {}):
        return result
    if state["status"] != "needs_input" or not (state.get("issue") or {}).get("url"):
        return result
    pending = [item for item in state.get("human_requests", [])
               if item.get("kind") == "clarification" and item.get("status") == "pending"]
    if not pending:
        return result
    if (pending[-1].get("delivery") or {}).get("status") in {"delivered", "reused"}:
        return result
    try:
        posted = human.post_issue(run_id, store.root)
    except Exception as exc:
        current = store.get(run_id)
        result = snapshot(store, current, settings)
        result["auto_comment"] = {
            "status": "failed", "error_type": type(exc).__name__,
            "reason": str(exc) if isinstance(exc, (StateError, ForgeError)) else None}
        return result
    result = snapshot(store, store.get(run_id), settings)
    result["auto_comment"] = {"status": "posted", "delivery": posted.get("delivery")}
    return result


def drive(store, state, settings, *, model=None, stop_after=None):
    execute_run(store, state, settings, model=model, stop_after=stop_after)
    return snapshot(store, state, settings)


def decide(run_id, root, *, commit, approved, reason):
    if not reason.strip():
        raise StateError("reason_required")
    store = JobStore(root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        if state["status"] != "awaiting_approval":
            raise StateError("not_waiting_for_approval")
        if commit != state["reviewed_commit"] or approval_digest(state) != state["approval_digest"]:
            raise StateError("stale_approval")
        problems = approval_problems(state)
        if problems:
            raise StateError("review_evidence_incomplete")
        attach(state, store.root).assert_clean(commit)
        knowledge.validate(state, store.root)
        request = human.approval_request(state, store.root)
        resolve_request(state, request, action="approve" if approved else "reject", text=reason)
        state["approval"] = {"commit": commit, "digest": state["approval_digest"], "approved": approved,
                             "reason": reason, "at": timestamp()}
        state["status"], state["stop_reason"] = ("approved", "human_approved") if approved else ("rejected", "human_rejected")
        store.save(state)
        return {"run_id": run_id, "status": state["status"], "approval": state["approval"]}


def recover(run_id, root, *, commit, reason):
    """显式接纳中断现场；无效审核报告只允许在原固定版本上补跑。"""
    if not reason.strip():
        raise StateError("reason_required")
    store = JobStore(root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        invalid_review = (not state["in_flight"] and state["phase"] == "review"
                          and state["status"] == "review_inconclusive"
                          and state["stop_reason"] == "invalid_review_report")
        if not state["in_flight"] and not invalid_review:
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
        if invalid_review:
            if state.get("reviewed_commit") != commit or state.get("expected_head") != commit:
                raise StateError("recovery_commit_mismatch")
            if source_snapshot(workspace.repository()) != state.get("workspace_snapshot"):
                raise StateError("reviewed_tree_changed")
        request = human.recovery_request(state, store.root)
        resolve_request(state, request, action="recover", text=reason)
        state["recovery"] = {"commit": commit, "reason": reason, "phase": state["phase"], "at": timestamp(),
                             "from_status": state["status"], "from_stop_reason": state["stop_reason"]}
        state["in_flight"] = None
        state["status"], state["stop_reason"] = "paused", "operator_recovery"
        # 明确接纳中间改动；只有实现阶段允许从未提交的现场继续。
        if state["phase"] in {"implement", "rework", "anchor", "base_update"}:
            state["expected_head"] = None
        state["workspace_snapshot"] = source_snapshot(workspace.repository())
        store.save(state)
        return {"run_id": run_id, "status": "paused", "recovery": state["recovery"]}


def snapshot(store, state, settings):
    directory = store.root / "runs" / state["run_id"]
    directory.mkdir(parents=True, exist_ok=True)
    trace = Trace(directory, settings.api_key.get_secret_value())
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
        "human_requests": state.get("human_requests", []),
        "requirements_revision": state.get("requirements_revision", 0),
        "question_batches": state.get("question_batches", []),
        "question_handovers": state.get("question_handovers", []),
        "question_limit": state.get("question_limit"),
        "task_brief": state.get("task_brief"),
        "plan": state.get("plan"),
        "plan_history": state.get("plan_history", []),
        "plan_status": planning.status(state),
        "plan_progress": state.get("plan_progress"),
        "plan_refresh_history": state.get("plan_refresh_history", []),
        "plan_refresh_required": bool(state.get("plan_refresh_required")),
        "subtask_mappings": state.get("subtask_mappings", []),
        "parallel_runs": state.get("parallel_runs", []),
        "integrations": state.get("integrations", []),
        "parallel_blocked": state.get("parallel_blocked", []),
        "pending_integration": state.get("pending_integration"),
        "implementation_slots": SlotManager(store.root,
            limit=settings.max_implementation_slots).snapshot(),
        "platform_preflight": state.get("platform_preflight"),
        "knowledge_snapshot_id": (state.get("knowledge_snapshot") or {}).get("snapshot_id"),
        "decision_records": state.get("decision_records", []),
        "output_directory": str(directory), "issue": state["issue"], "approval": state["approval"],
        "merge": state.get("merge"),
        "changes": None if changes is None else {k: changes[k] for k in
            ("changed_files", "additions", "deletions", "base_commit", "head_commit", "worktree")},
        "repository_unchanged": None if state["before"] is None else state["before"] == source_snapshot(Path(state["task"]["repository"])),
        "review": {"enabled": state["review_enabled"] and state["workspace_enabled"],
            "review_rounds": len(state["history"]), "rework_attempts": state["reworks"],
            "reviewed_commit": state["reviewed_commit"], "verdict": None if review is None else review["verdict"],
            "findings": 0 if review is None else len(review["findings"]), "limit": review_limit(state, settings),
            "final_commit": None if changes is None else changes["head_commit"],
            "perspectives": {} if review is None else {
                name: value.get("verdict")
                for name, value in (review.get("perspectives") or {}).items()},
            "blocking_findings": 0 if review is None else sum(
                1 for item in review.get("findings") or []
                if item.get("blocking") and item.get("status") in {"open", "unverified", "disputed"}),
            "non_blocking_findings": 0 if review is None else sum(
                1 for item in review.get("findings") or [] if not item.get("blocking")),
            "disputes": len(state.get("disputes") or []),
            "open_disputes": sum(1 for item in state.get("disputes") or []
                                 if item.get("outcome") in {"pending", "undetermined"}),
            "rework_limit": review_limit(state, settings),
            "rework_extensions": len(state.get("rework_extensions") or []),
            "basis": (state.get("review_basis") or {}).get("hash"),
            "reused": list(state.get("review_reuse") or [])[-1:]},
        "review_rounds": state.get("review_rounds") or [],
        "review_basis": state.get("review_basis"),
        "review_invalidations": state.get("review_invalidations") or [],
        "disputes": state.get("disputes") or [],
        "rework_limit": review_limit(state, settings),
        "rework_extensions": state.get("rework_extensions") or [],
        "prior_attempt": state.get("prior_attempt"),
    }
    trace.save("summary.json", summary)
    return json.loads(trace.encode(summary))
