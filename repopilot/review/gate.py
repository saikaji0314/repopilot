"""审核证据查看、争议人工判断与返工额度明确延长；不调用模型或平台。"""

from repopilot.collaboration.requests import all_requests, resolve_request
from repopilot.core.config import Task
from repopilot.orchestration import human
from repopilot.orchestration import knowledge
from repopilot.orchestration.state import attach
from repopilot.storage.jobs import JobStore, StateError, job_lock, timestamp

from . import disputes, merge, outcome


def show(run_id, root):
    state = JobStore(root).get(run_id)
    report = state.get("review_report") or {}
    return {
        "status": "ok", "run_id": run_id, "review": {
            "enabled": state.get("review_enabled"),
            "basis": state.get("review_basis"),
            "verdict": report.get("verdict"),
            "summary": report.get("summary"),
            "rework_attempts": state.get("reworks", 0),
            "rework_limit": outcome.limit(state),
            "rework_extensions": state.get("rework_extensions") or [],
            "rounds": state.get("review_rounds") or [],
            "disputes": state.get("disputes") or [],
            "invalidations": state.get("review_invalidations") or [],
            "reuse": state.get("review_reuse") or [],
        },
        "pending": [request.model_dump(mode="json") for request in all_requests(state)
                    if request.status == "pending"],
    }


def decide(run_id, root, *, dispute_id, decision, reason):
    if decision not in {"confirmed", "refuted"}:
        raise StateError("dispute_decision_invalid")
    if not reason.strip():
        raise StateError("reason_required")
    store = JobStore(root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        request = _pending_dispute_request(state, dispute_id)
        if state["status"] != "needs_input" or state.get("in_flight"):
            raise StateError("dispute_not_waiting")
        context = {**human.capture_context(state, root), "dispute": dispute_id}
        if context != request.context:
            raise StateError("stale_human_request")
        knowledge.validate(state, root)
        try:
            disputes.apply_human(state, dispute_id, decision=decision, reason=reason)
        except ValueError as exc:
            raise StateError(str(exc)) from None
        resolve_request(state, request, action="decide", text=reason)
        _mark_request(state, dispute_id, request.id, decision, reason)
        state.setdefault("review_dispute_decisions", []).append({
            "dispute": dispute_id, "request": request.id, "decision": decision,
            "reason": reason, "at": timestamp()})
        remaining = [row for row in _pending_disputes(state)
                     if row.get("outcome") == "undetermined"]
        if remaining:
            state["status"], state["stop_reason"] = "needs_input", "review_dispute_requires_decision"
        else:
            _continue_after_disputes(state, root)
        store.save(state)
        return {"status": state["status"], "run_id": run_id, "dispute": dispute_id,
                "decision": decision, "verdict": (state.get("review_report") or {}).get("verdict")}


def extend(run_id, root, *, extra, reason):
    if not reason.strip():
        raise StateError("reason_required")
    store = JobStore(root)
    with job_lock(store.root, run_id):
        state = store.get(run_id)
        if state["status"] not in {"rework_limit_reached"} or state.get("in_flight"):
            raise StateError("rework_limit_not_reached")
        workspace = attach(state, root)
        if workspace is not None and state.get("reviewed_commit"):
            workspace.assert_clean(state["reviewed_commit"])
        request = next((item for item in all_requests(state)
                        if item.kind == "handover" and item.status == "pending"), None)
        if request is not None:
            resolve_request(state, request, action="decide", text=reason)
        new_limit = outcome.extend(state, extra, reason)
        state["phase"], state["rework_pending"] = "rework", True
        state["status"], state["stop_reason"] = "paused", "rework_budget_extended"
        store.save(state)
        return {"status": state["status"], "run_id": run_id, "rework_limit": new_limit,
                "rework_attempts": state.get("reworks", 0), "extensions": state["rework_extensions"]}


def _pending_dispute_request(state, dispute_id):
    for request in all_requests(state):
        if (request.kind == "dispute" and request.status == "pending"
                and any(item.get("dispute") == dispute_id for item in request.blocking)):
            return request
    raise StateError("dispute_request_not_found")


def _pending_disputes(state):
    ids = [request.blocking[0].get("dispute") for request in all_requests(state)
           if request.kind == "dispute" and request.status == "pending" and request.blocking]
    result = []
    for identifier in ids:
        try:
            result.append(disputes.find(state, identifier))
        except KeyError:
            continue
    return result


def _mark_request(state, dispute_id, request_id, decision, reason):
    for row in state.get("disputes") or []:
        if row.get("id") == dispute_id:
            row["human_request_id"] = request_id
            row.setdefault("human", {}).update(
                {"decision": decision, "reason": reason, "request": request_id,
                 "at": timestamp()})


def _continue_after_disputes(state, root):
    report = state.get("review_report") or {}
    verdict = merge.verdict_from(
        {name: value.get("verdict") for name, value in (report.get("perspectives") or {}).items()},
        report.get("findings") or [])
    report["verdict"] = verdict
    task = Task.model_validate(state["task"])
    outcome.apply(state, root, task, verdict, summary=report.get("summary") or "争议已人工判断",
                  questions=report.get("questions") or [])
    if state["status"] == "needs_input" and state.get("stop_reason") != "review_dispute_requires_decision":
        return
    if verdict == "changes_requested" and state["phase"] == "rework":
        state["status"], state["stop_reason"] = "paused", "review_dispute_decided"
