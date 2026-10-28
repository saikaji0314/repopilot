"""一轮双视角审核的编排：固定快照、并行报告、合并、争议复核与持久化。

恢复策略：
- 先保存固定依据，再启动两个只读视角；
- 每份完成的视角报告立即落盘，恢复时只补未完成的视角；
- 合并结果作为草稿保存，A／B／C 结论逐条写回，再形成最终轮次记录；
- 同一提交、同一依据已有结论时直接复用，不重新调用模型。
"""

from repopilot.agents.reports import ReviewReport
from repopilot.orchestration import human
from repopilot.storage.jobs import StateError, timestamp

from . import basis as basis_store
from . import disputes, merge, outcome, runner
from .contracts import PERSPECTIVES, PERSPECTIVE_LABELS, MergedFinding

FOCUS = {
    "correctness": "重点核对边界、调用方、回归、检查事实与实现错误；业务遗漏由另一视角负责。",
    "context": "重点对照原需求、人工答复、验收、确认决定与项目约定；也可以读代码核对。",
}


def _previous_findings(state) -> list[MergedFinding]:
    if not state.get("review_rounds"):
        return []
    previous = []
    for item in state["review_rounds"][-1].get("findings") or []:
        try:
            finding = MergedFinding.model_validate(item)
        except ValueError:
            continue
        if finding.status not in {"closed", "invalid"}:
            previous.append(finding)
    return previous


def _perspective_payload(shared, *, state, basis, round_number, perspective, previous, workspace):
    data = dict(shared)
    data["review"] = {
        "perspective": perspective, "label": PERSPECTIVE_LABELS[perspective],
        "round": round_number, "independent": not previous,
        "commit": basis["commit"], "base_commit": basis["base_commit"],
        "basis_hash": basis["hash"],
    }
    data["review_focus"] = FOCUS[perspective]
    if previous:
        data["previous_findings"] = [finding.model_dump(mode="json") for finding in previous]
        data["rework"] = {
            "attempt": state.get("reworks", 0), "limit": outcome.limit(state),
            "implementation_summary": str((state.get("report") or {}).get("summary") or ""),
        }
        last_commit = (state["review_rounds"][-1] or {}).get("commit")
        if last_commit:
            data["changes_since_last_review"] = workspace.diff_since(last_commit)
    else:
        data["review"]["instruction"] = ("首轮不要引用实现方自我说明，也不要假设会看到另一位审核者的结论；"
                                         "以固定提交、差异、独立检查和确认资料为准。")
    return data


def run(*, state, task, settings, control, trace, workspace, store_root, model, materials,
        shared_payload, checkpoint, run_pair=None, run_dispute_pair=None, run_arbitration=None,
        session_factory=None):
    run_pair = run_pair or runner.run_pair
    run_dispute_pair = run_dispute_pair or runner.run_dispute_pair
    run_arbitration = run_arbitration or runner.run_arbitration
    root = workspace.repository()
    workspace.assert_clean(state["reviewed_commit"])
    basis = basis_store.snapshot(state)
    reused = basis_store.reusable_round(state, basis)
    if reused is not None:
        state["review_basis"] = basis
        return _reuse(state, store_root, reused, basis, trace, checkpoint)
    state["review_basis"] = basis

    round_number = len(state.get("review_rounds") or []) + 1
    previous = _previous_findings(state)
    pending = state.get("review_pending") or {}
    if (pending.get("basis") or {}).get("hash") != basis["hash"]:
        pending = {"round": round_number, "basis": basis, "commit": basis["commit"],
                   "reports": {}, "at": timestamp()}
        state["review_pending"] = pending
        checkpoint()
    reports = {}
    for name, data in (pending.get("reports") or {}).items():
        try:
            reports[name] = ReviewReport.model_validate(data)
        except ValueError:
            continue
    missing = [name for name in PERSPECTIVES if name not in reports]
    if missing:
        payloads = {name: _perspective_payload(shared_payload, state=state, basis=basis,
                                               round_number=round_number, perspective=name,
                                               previous=previous, workspace=workspace)
                    for name in missing}

        def on_report(name, report):
            pending.setdefault("reports", {})[name] = report.model_dump(mode="json")
            state["review_pending"] = pending
            checkpoint()

        reports.update(run_pair(task=task, settings=settings, control=control, trace=trace,
                                root=root, payloads=payloads, model=model, materials=materials,
                                perspectives=missing, on_report=on_report,
                                session_factory=session_factory))
    trace.event("review_perspectives_finished", round=round_number, commit=basis["commit"],
                verdicts={name: report.verdict for name, report in reports.items()})

    draft = state.get("review_draft")
    if not ((draft or {}).get("basis") or {}).get("hash") == basis["hash"]:
        draft = _build_draft(state, round_number, basis, reports, previous)
        state["review_draft"] = draft
        checkpoint()
    # 检查点会重新编码并替换状态对象，之后必须从 state 重新取引用。
    draft = state["review_draft"]
    findings = [MergedFinding.model_validate(item) for item in draft["findings"]]
    trace.event("review_merged", round=round_number, findings=len(findings),
                blocking=sum(1 for item in findings if item.blocking and item.status != "closed"),
                sources=sum(len(item.sources) for item in findings))

    _prepare_disputes(state=state, draft=state["review_draft"])
    _run_disputes(state=state, task=task, settings=settings, control=control, trace=trace,
                  root=root, model=model, materials=materials, shared_payload=shared_payload,
                  run_dispute_pair=run_dispute_pair,
                  run_arbitration=run_arbitration, checkpoint=checkpoint,
                  session_factory=session_factory)

    draft = state["review_draft"]
    findings = [MergedFinding.model_validate(item) for item in draft["findings"]]
    perspective_verdicts = {name: data.get("verdict") for name, data in draft["perspectives"].items()}
    verdict = merge.verdict_from(perspective_verdicts,
                                 [finding.model_dump(mode="json") for finding in findings])
    summary = merge.combine_summary(reports, findings, verdict)
    questions = merge.response_questions(reports)
    undetermined = _undetermined(state, findings, basis)
    report, history, record = _finalize(state, draft, basis, reports, findings, verdict,
                                        summary, questions, undetermined)
    state["review_rounds"].append(record)
    state["history"].append(history)
    state["review_report"] = report
    state["review_basis"] = basis
    state.pop("review_pending", None)
    state.pop("review_draft", None)
    # 请求上下文必须包含最终轮次记录；否则人工判断时会因版本前进被判为过期请求。
    for dispute_id in undetermined:
        row = disputes.find(state, dispute_id)
        request = human.wait_for_dispute(state, store_root, dispute=row)
        row["human_request_id"] = request.id
        row.setdefault("outcomes", []).append({"request": request.id, "at": timestamp()})
    checkpoint()
    trace.save("review.json", report)
    trace.save("review-history.json", state["history"])
    trace.event("review_finished", verdict=verdict, commit=basis["commit"],
                findings=len([item for item in findings if item.blocking
                              and item.status in {"open", "unverified", "disputed"}]),
                disputes=len(undetermined), round=round_number)
    return {"verdict": verdict, "summary": summary, "questions": questions,
            "reused": False, "round": round_number, "undetermined": list(undetermined)}


def _build_draft(state, round_number, basis, reports, previous):
    merged = merge.merge_findings(round_number, reports)
    updated, conflicts, _ = merge.resolve_previous(round_number, previous, reports, merged)
    draft = {"round": round_number, "complete": False, "commit": basis["commit"],
             "basis": basis, "at": timestamp(),
             "perspectives": {name: report.model_dump(mode="json")
                              for name, report in reports.items()},
             "findings": [item.model_dump(mode="json") for item in [*updated, *merged]],
             "disputes": [], "conflicts": []}
    for finding, positions in conflicts:
        reused = disputes.reuse_outcome(state, finding, basis)
        if reused is None:
            record = disputes.create(state, round_number=round_number, finding=finding,
                                     positions=positions, basis=basis)
        else:
            record = reused[1]
        _link_conflict(draft, finding, positions, record)
        if record["id"] not in draft["disputes"]:
            draft["disputes"].append(record["id"])
        draft["conflicts"].append({"finding": finding.id, "dispute": record["id"]})
        existing = record.get("outcome")
        if existing in {"confirmed", "human_confirmed", "reused_confirmed"}:
            _set_finding(draft, finding.id, status="open",
                         resolution={"dispute": record["id"], "outcome": existing,
                                     "reused": True})
        elif existing in {"refuted", "human_refuted", "reused_refuted"}:
            _set_finding(draft, finding.id, status="invalid",
                         resolution={"dispute": record["id"], "outcome": existing,
                                     "reused": True})
    return draft


def _link_conflict(draft, finding, positions, record):
    for item in draft["findings"]:
        if item["id"] != finding.id:
            continue
        item["status"] = "disputed"
        item["resolution"] = {"round": finding.round, "responses": positions,
                              "conflict": True, "dispute": record["id"]}


def _prepare_disputes(*, state, draft):
    """为同键已有结论的争议直接复用；新的争议保持待复核。"""

    for entry in draft.get("conflicts") or []:
        record = disputes.find(state, entry["dispute"])
        outcome_name = record.get("outcome")
        if outcome_name in {"confirmed", "human_confirmed", "reused_confirmed"}:
            _set_finding(draft, entry["finding"], status="open",
                         resolution={"dispute": record["id"], "outcome": outcome_name,
                                     "reused": True})
        elif outcome_name in {"refuted", "human_refuted", "reused_refuted"}:
            _set_finding(draft, entry["finding"], status="invalid",
                         resolution={"dispute": record["id"], "outcome": outcome_name,
                                     "reused": True})


def _run_disputes(*, state, task, settings, control, trace, root, model, materials,
                  shared_payload, run_dispute_pair, run_arbitration, checkpoint,
                  session_factory=None):
    for entry in list((state.get("review_draft") or {}).get("conflicts") or []):
        record = disputes.find(state, entry["dispute"])
        if record.get("outcome") != "pending":
            continue
        base = dict(shared_payload)
        base.pop("previous_findings", None)
        base.pop("rework", None)
        missing = [side for side in ("a", "b") if record.get(side) is None]
        if missing:
            def on_report(side, report, dispute_id=record["id"]):
                current = disputes.find(state, dispute_id)
                current[side] = report.model_dump(mode="json")
                state["disputes"] = [current if item.get("id") == dispute_id else item
                                     for item in state["disputes"]]
                checkpoint()

            run_dispute_pair(task=task, settings=settings, control=control, trace=trace,
                root=root, payloads={side: disputes.payload(record, base, side=side)
                                     for side in missing},
                model=model, materials=materials, sides=missing, on_report=on_report,
                session_factory=session_factory)
            record = disputes.find(state, entry["dispute"])
            trace.event("dispute_ab_finished", dispute=record["id"],
                        a=(record.get("a") or {}).get("judgement"),
                        b=(record.get("b") or {}).get("judgement"))
        record = disputes.find(state, entry["dispute"])
        if record.get("a") is None or record.get("b") is None:
            raise StateError("dispute_ab_incomplete")
        if record.get("c") is None:
            arbitration_payload = disputes.payload(record, base,
                reports={"a": record["a"], "b": record["b"]})
            arbitration = run_arbitration(task=task, settings=settings, control=control,
                trace=trace, root=root, payload=arbitration_payload, model=model,
                materials=materials, session_factory=session_factory)
            record = disputes.find(state, entry["dispute"])
            record["c"] = arbitration.model_dump(mode="json")
            state["disputes"] = [record if item.get("id") == record["id"] else item
                                 for item in state["disputes"]]
            checkpoint()
            trace.event("dispute_arbitration_finished", dispute=record["id"],
                        outcome=arbitration.outcome)
        record = disputes.find(state, entry["dispute"])
        disputes.apply_reports(state, record["id"], a=record["a"], b=record["b"],
                               c=record["c"])
        checkpoint()


def _set_finding(draft, finding_id, *, status, resolution):
    for item in draft["findings"]:
        if item.get("id") == finding_id:
            item["status"] = status
            item["blocking"] = (item.get("severity") in {"blocker", "major"}
                                and status not in {"closed", "invalid"})
            item["resolution"] = resolution


def _undetermined(state, findings, basis) -> list[str]:
    records = {(record["key"], record.get("commit"), record.get("basis_hash")): record
               for record in state.get("disputes") or []}
    result = []
    for finding in findings:
        if finding.status != "disputed":
            continue
        record = records.get((finding.key, basis.get("commit"), basis.get("hash")))
        if record is not None and record.get("outcome") == "undetermined":
            result.append(record["id"])
    return result


def _finalize(state, draft, basis, reports, findings, verdict, summary, questions, undetermined):
    dispute_records = [disputes.find(state, dispute_id) for dispute_id in draft.get("disputes") or []]
    decisions = []
    for report in reports.values():
        for decision in report.decisions:
            view = decision.model_dump(mode="json")
            if view not in decisions:
                decisions.append(view)
    for record in dispute_records:
        for decision in (record.get("c") or {}).get("decisions") or []:
            if decision not in decisions:
                decisions.append(decision)
    report = {
        "verdict": verdict, "summary": summary, "questions": questions,
        "findings": [finding.model_dump(mode="json") for finding in findings],
        "perspectives": {name: {"verdict": data.get("verdict"), "summary": data.get("summary"),
                                "findings": data.get("findings") or [],
                                "responses": data.get("responses") or [],
                                "questions": data.get("questions") or []}
                         for name, data in draft["perspectives"].items()},
        "disputes": dispute_records,
        "decisions": decisions,
        "non_blocking": [finding.id for finding in findings if not finding.blocking],
        "round": draft["round"], "basis": basis, "rework": state.get("reworks", 0),
        "rework_limit": outcome.limit(state), "at": timestamp(),
    }
    history = {
        "attempt": draft["round"] - 1, "round": draft["round"], "commit": basis["commit"],
        "knowledge_snapshot_id": (state.get("knowledge_snapshot") or {}).get("snapshot_id"),
        "plan_version": (state.get("plan") or {}).get("version"),
        "verdict": verdict, "summary": summary,
        "findings": [merge.finding_view(finding) for finding in findings],
        "perspectives": {name: data.get("verdict") for name, data in draft["perspectives"].items()},
        "disputes": list(undetermined), "basis_hash": basis["hash"],
    }
    record = {**draft, "complete": True, "verdict": verdict, "summary": summary,
              "questions": questions, "report": report, "history": history,
              "findings": [finding.model_dump(mode="json") for finding in findings],
              "perspectives": history["perspectives"], "at": timestamp(),
              "verified_sha256": basis["verified_sha256"]}
    return report, history, record


def _reuse(state, root, reused, basis, trace, checkpoint):
    report = reused["report"]
    if not any(item.get("round") == reused.get("round") for item in state.get("history") or []):
        state["history"].append({
            "attempt": (reused.get("round") or 1) - 1, "round": reused.get("round"),
            "commit": basis["commit"], "verdict": report.get("verdict"),
            "summary": report.get("summary"), "findings": report.get("findings") or [],
            "perspectives": {name: data.get("verdict")
                             for name, data in (report.get("perspectives") or {}).items()},
            "reused": True})
    state["review_report"] = report
    state["review_basis"] = basis
    state.pop("review_pending", None)
    state.pop("review_draft", None)
    for dispute_id in _undetermined(
            state, [MergedFinding.model_validate(item) for item in report.get("findings") or []],
            basis):
        row = disputes.find(state, dispute_id)
        request = human.wait_for_dispute(state, root, dispute=row)
        row["human_request_id"] = request.id
    checkpoint()
    trace.event("review_reused", round=reused.get("round"), commit=basis["commit"],
                basis_hash=basis["hash"], verdict=report.get("verdict"))
    trace.event("review_finished", verdict=report.get("verdict"), commit=basis["commit"],
                findings=sum(1 for item in report.get("findings") or []
                             if item.get("blocking") and item.get("status") in
                             {"open", "unverified", "disputed"}),
                disputes=sum(1 for item in report.get("disputes") or []
                             if item.get("outcome") == "undetermined"), reused=True,
                round=reused.get("round"))
    trace.save("review.json", report)
    trace.save("review-history.json", state["history"])
    return {"verdict": report.get("verdict"), "summary": report.get("summary"),
            "questions": report.get("questions") or [], "reused": True,
            "round": reused.get("round"), "undetermined": []}
