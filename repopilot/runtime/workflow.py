"""业务阶段的持久化状态机；模型内部循环仍由 LangGraph 执行。"""

from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
from threading import RLock
import time
from uuid import uuid4

from langchain.agents.structured_output import StructuredOutputValidationError
from pydantic import ValidationError

from . import agent as engine
from .budget import Budget, RunLimit
from .config import RuntimeSettings, Task
from .store import JobStore, StateError, job_lock, timestamp
from .workspace import Workspace, WorkspaceError, head_commit, source_snapshot


TERMINAL = {"completed", "published", "rejected", "workspace_error", "phase_error", "failed",
            "limit_reached", "review_inconclusive", "rework_limit_reached", "needs_input"}


def canonical_hash(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


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
        frozen = RuntimeSettings.model_validate({**state["settings"], **(overrides or {}), "api_key": settings.api_key})
        return start(Task.model_validate(state["task"]), frozen, root, model=model,
            workspace=state["workspace_enabled"], review=state["review_enabled"],
            require_approval=state["require_approval"], issue=state["issue"], stop_after=stop_after,
            retry_of=run_id, retry_reason=reason, base_commit=base_commit)


def attach(state, root):
    if not state["workspace_enabled"] or not state["base_commit"]:
        return None
    workspace = Workspace(Path(state["task"]["repository"]), root / "worktrees",
                          path=root / "worktrees" / state["run_id"], base_commit=state["base_commit"])
    return workspace


def resume(run_id, settings, output_root, *, model=None, stop_after=None):
    store = JobStore(output_root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        settings = RuntimeSettings.model_validate({**state["settings"], "api_key": settings.api_key})
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
    task = Task.model_validate(state["task"])
    directory = store.root / "runs" / state["run_id"]
    directory.mkdir(parents=True, exist_ok=True)
    trace = engine.Trace(directory, settings.api_key.get_secret_value())
    budget = Budget(store.root / "budget.sqlite3", settings.budget_cny)
    control = engine.RunControl(settings, budget, trace, state["run_id"])
    saved = state["control"]
    for name in ("model_calls", "tool_calls", "input_tokens", "output_tokens", "accounted_cny", "checks", "progress"):
        if name in saved:
            setattr(control, name, saved[name])
    control.model_calls = max(control.model_calls, budget.snapshot(state["run_id"])["calls"])
    control.accounted_cny = max(control.accounted_cny, budget.snapshot(state["run_id"])["accounted_cny"])
    edits = engine.EditBudget(settings.max_edits)
    edits.count, edits.touched = state["edits"].get("edits", 0), state["edits"].get("edited_files", [])
    previous_active, started = state["active_seconds"], time.monotonic()
    control.deadline = started + max(0, settings.run_timeout_seconds - previous_active)
    saving = RLock()

    def checkpoint():
        with saving:
            state["control"] = {name: getattr(control, name) for name in
                ("model_calls", "tool_calls", "input_tokens", "output_tokens", "accounted_cny", "checks", "progress")}
            state["edits"] = edits.snapshot()
            state["active_seconds"] = previous_active + time.monotonic() - started
            # 模型输出偶然回显当前密钥时，SQLite 与 JSON 使用同一脱敏规则。
            cleaned = json.loads(trace.encode(state))
            state.clear()
            state.update(cleaned)
            store.save(state)

    trace.on_event = checkpoint
    trace.save("input.json", {"task": state["task"], "settings": state["settings"],
        "issue": state["issue"], "versions": {n: version(n) for n in
            ("langchain", "langgraph", "langchain-deepseek")}})
    usable_model = engine.make_model(settings) if model is None else model
    workspace = attach(state, store.root)
    state["status"], state["stop_reason"] = "running", "running"
    try:
        if workspace and state["phase"] != "prepare":
            workspace.prepare()
            if state.get("workspace_snapshot"):
                if source_snapshot(workspace.repository()) != state["workspace_snapshot"]:
                    raise WorkspaceError("checkpoint_tree_changed")
            elif state["expected_head"]:
                workspace.assert_clean(state["expected_head"])
        while state["status"] == "running":
            control.remaining()
            phase = state["phase"]
            state["in_flight"] = phase
            checkpoint()
            if phase == "prepare":
                if state["workspace_enabled"]:
                    if not state["base_commit"]:
                        state["base_commit"] = head_commit(task.repository)
                    if not state["before"]:
                        state["before"] = source_snapshot(task.repository)
                    checkpoint()
                    workspace = attach(state, store.root).prepare()
                    state["expected_head"] = state["base_commit"]
                trace.event("workspace_ready", worktree=str(task.repository if workspace is None else workspace.path),
                            base_commit=state["base_commit"])
                state["phase"] = "implement"
            elif phase in {"implement", "rework"}:
                root = task.repository if workspace is None else workspace.repository()
                tools = engine.build_tools(task, control.remaining, workspace=None if workspace is None else root,
                                           edits=None if workspace is None else edits)
                payload = {"request": task.request, "available_checks": list(task.checks)}
                if phase == "rework":
                    if state["rework_pending"]:
                        state["reworks"] += 1
                        state["rework_pending"] = False
                    payload.update(review_feedback=state["history"][-1]["findings"],
                                   reviewed_commit=state["reviewed_commit"])
                    trace.event("rework_started", attempt=state["reworks"], findings=len(state["history"][-1]["findings"]))
                if state.get("recovery"):
                    payload["recovery"] = state["recovery"]
                report = engine.run_phase(prompt=engine.SYSTEM_PROMPT, schema=engine.ImplementationReport,
                    payload=payload, tools=tools, settings=settings, control=control, model=usable_model, phase=phase)
                report = engine.consistent_status(report, workspace.changes() if workspace else {})
                state["report"] = report.model_dump(mode="json")
                trace.save("report.json", state["report"])
                if report.status != "implemented" or workspace is None or not state["review_enabled"]:
                    trace.event("review_skipped", reason=f"{'implementer' if phase == 'implement' else phase}_status_{report.status}")
                    if phase == "rework":
                        state["status"], state["stop_reason"] = "phase_error", f"rework_status_{report.status}"
                    else:
                        state["status"], state["stop_reason"] = "completed", "report"
                else:
                    state["phase"] = "anchor"
            elif phase == "anchor":
                anchor = workspace.commit(f"chore: 运行 {state['run_id']} 的实现改动")
                state["reviewed_commit"] = state["expected_head"] = anchor["commit"]
                state["phase"] = "verify"
            elif phase == "verify":
                workspace.assert_clean(state["reviewed_commit"])
                state["verified"] = engine.verify_checks(task, workspace.repository(), control, trace, control.remaining)
                workspace.assert_clean(state["reviewed_commit"])
                trace.save("verification.json", state["verified"])
                state["phase"] = "review"
            elif phase == "review":
                workspace.assert_clean(state["reviewed_commit"])
                diff = workspace.diff()
                payload = {"request": task.request, "available_checks": list(task.checks),
                    "review_target": {"commit": state["reviewed_commit"], "base_commit": state["base_commit"]},
                    "changed_files": [c["path"] for c in workspace.changes()["files"]],
                    "verified_checks": state["verified"], "diff": diff["diff"], "diff_truncated": diff["truncated"]}
                if state["history"]:
                    payload["previous_findings"] = state["history"][-1]["findings"]
                trace.event("review_started", commit=state["reviewed_commit"])
                report = engine.run_phase(prompt=engine.REVIEW_PROMPT, schema=engine.ReviewReport,
                    payload=payload, tools=engine.build_tools(task, control.remaining, workspace=workspace.path),
                    settings=settings, control=control, model=usable_model, phase="review")
                workspace.assert_clean(state["reviewed_commit"])
                if report.verdict == "approved" and not engine.verification_passed(task, state["verified"]):
                    report = report.model_copy(update={"verdict": "unable_to_review",
                        "summary": "本地复核未全部通过，拒绝模型批准。"})
                state["review_report"] = report.model_dump(mode="json")
                trace.save("review.json", state["review_report"])
                state["history"].append({"attempt": len(state["history"]), "commit": state["reviewed_commit"],
                    "verdict": report.verdict, "summary": report.summary,
                    "findings": engine.findings_brief(report.findings)})
                trace.save("review-history.json", state["history"])
                trace.event("review_finished", verdict=report.verdict, commit=state["reviewed_commit"])
                if report.verdict == "unable_to_review":
                    state["status"], state["stop_reason"] = "review_inconclusive", "review_unable_to_review"
                elif report.verdict == "changes_requested":
                    if len(state["history"]) > settings.max_rework:
                        state["status"], state["stop_reason"] = "rework_limit_reached", "max_rework"
                    else:
                        state["phase"], state["rework_pending"] = "rework", True
                else:
                    state["status"] = "awaiting_approval" if state["require_approval"] else "completed"
                    state["stop_reason"] = state["status"] if state["require_approval"] else "report"
                    state["phase"] = "approval"
                    state["approval_digest"] = approval_digest(state)
            else:
                raise StateError("unknown_phase")
            state["in_flight"] = None
            if workspace:
                state["workspace_snapshot"] = source_snapshot(workspace.repository())
            checkpoint()
            if stop_after == phase and state["status"] == "running":
                state["status"], state["stop_reason"] = "paused", "checkpoint"
                checkpoint()
    except (ValidationError, StructuredOutputValidationError) as exc:
        phase = state["phase"]
        state["status"] = "review_inconclusive" if phase == "review" else "phase_error"
        state["stop_reason"] = f"invalid_{phase}_report"
        state["in_flight"] = None
        if phase == "review":
            state["review_report"] = None
        trace.event("run_error", error_type=type(exc).__name__, phase=phase,
                    validation=engine.validation_details(exc))
    except RunLimit as exc:
        state["status"], state["stop_reason"] = "limit_reached", str(exc)
    except WorkspaceError as exc:
        state["status"], state["stop_reason"] = "workspace_error", str(exc)
    except KeyboardInterrupt:
        state["status"], state["stop_reason"] = "interrupted", "interrupted"
    except Exception as exc:
        state["status"], state["stop_reason"] = "failed", type(exc).__name__
        trace.event("run_error", error_type=type(exc).__name__, phase=state["phase"])
    checkpoint()
    trace.on_event = None
    return snapshot(store, state, settings)


def approval_digest(state):
    return canonical_hash({k: state.get(k) for k in
        ("task", "issue", "reviewed_commit", "review_report", "verified", "base_commit")})


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
