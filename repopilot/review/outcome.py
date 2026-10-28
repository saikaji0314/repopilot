"""审核结论如何推进任务：返工、批准、交接与争议等待；不调用模型。"""

from repopilot.agents import loop as engine
from repopilot.orchestration import human
from repopilot.orchestration.state import approval_digest

from . import merge


def limit(state) -> int:
    stored = state.get("rework_limit")
    if stored is not None:
        return max(0, int(stored))
    return int((state.get("settings") or {}).get("max_rework", 3))


def unavailable(task, state, verdict: str) -> str:
    """模型说通过也不能越过独立检查；检查失败时一律不能批准。"""

    if verdict == "approved" and not engine.verification_passed(task, state.get("verified") or []):
        return "unable_to_review"
    return verdict


def apply(state, root, task, verdict: str, *, summary: str = "",
          questions: list[str] | None = None) -> str:
    """按合并结论推进状态；验收失败、无法判断和到限都保留现场交人。"""

    requested = verdict
    verdict = unavailable(task, state, verdict)
    summary = summary or "审核结论"
    if verdict != requested:
        summary = "独立复核未全部通过，不能批准。"
        _mark_unable(state, summary)
    questions = list(questions or [])
    if verdict == "unable_to_review":
        if questions:
            human.wait_for_clarification(state, root, summary=summary, questions=questions,
                limit=int((state.get("settings") or {}).get("max_question_batches", 3)),
                blocking={"phase": "review"})
        else:
            state["status"], state["stop_reason"] = "review_inconclusive", "review_unable_to_review"
        return verdict
    if verdict == "changes_requested":
        if state.get("reworks", 0) >= limit(state):
            state["status"], state["stop_reason"] = "rework_limit_reached", "max_rework"
            human.wait_for_handover(state, root,
                summary=f"审核返工达到上限（{limit(state)} 次），仍有未解决的阻断发现。",
                findings=merge.open_findings(_report_findings(state)),
                limit=limit(state))
        else:
            state["phase"], state["rework_pending"] = "rework", True
        return verdict
    if verdict == "undetermined":
        state["status"] = "needs_input"
        state["stop_reason"] = "review_dispute_requires_decision"
        return verdict
    state["status"] = "awaiting_approval" if state.get("require_approval") else "completed"
    state["stop_reason"] = state["status"] if state.get("require_approval") else "report"
    state["phase"] = "approval"
    state["approval_digest"] = approval_digest(state)
    if state.get("require_approval"):
        human.approval_request(state, root)
    return verdict


def _report_findings(state) -> list[merge.MergedFinding]:
    findings = []
    for item in (state.get("review_report") or {}).get("findings") or []:
        try:
            findings.append(merge.MergedFinding.model_validate(item))
        except ValueError:
            continue
    return findings


def _mark_unable(state, summary: str) -> None:
    """程序否决模型批准时同步证据，不能让 review_report 继续写 approved。"""

    report = state.get("review_report")
    if not report:
        return
    report["verdict"] = "unable_to_review"
    report["summary"] = summary
    for container in (state.get("review_rounds") or [])[-1:]:
        container["verdict"] = "unable_to_review"
        if isinstance(container.get("report"), dict):
            container["report"]["verdict"] = "unable_to_review"
    for container in (state.get("history") or [])[-1:]:
        container["verdict"] = "unable_to_review"


def extend(state, extra: int, reason: str) -> int:
    """负责人明确决定继续时增加修改额度；保留原次数和历史。"""

    extra = int(extra)
    if extra < 1:
        raise ValueError("extra_rework_must_be_positive")
    current = limit(state)
    new_limit = current + extra
    state["rework_limit"] = new_limit
    state.setdefault("rework_extensions", []).append({
        "from": current, "to": new_limit, "extra": extra, "reason": reason,
        "reworks": state.get("reworks", 0),
        "review_rounds": len(state.get("history") or []),
        "at": human.timestamp(),
    })
    return new_limit
