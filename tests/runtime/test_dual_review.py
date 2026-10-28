"""M5 双视角审核、发现合并、争议 A／B＋C、修改上限与依据复用。

模型调用由确定性脚本替身替换；Git、SQLite、工作区和 CLI 都是真实本地组件。
"""

import json
from pathlib import Path
import threading

import pytest

import repopilot.cli as cli
from repopilot.agents.reports import ArbitrationReport, DisputeReviewReport, ReviewReport
from repopilot.orchestration import human, workflow
from repopilot.review import basis as review_basis
from repopilot.review import gate, merge, outcome, runner
from repopilot.storage.jobs import JobStore, StateError
from tests.runtime.test_review_loop import (
    FakeModel, implement_done, implement_fix_report, setup, turn,
)


def finding(observation="缺少空标题分支", *, path="app.py", line=1, severity="major",
            evidence_status="source_inference", suggestion="补空标题分支",
            basis="code", reference="", requirement="空标题必须有明确行为",
            trigger="标题为空字符串", impact="行为未定义"):
    item = {"basis": basis, "severity": severity, "evidence_status": evidence_status,
            "requirement": requirement, "trigger": trigger, "impact": impact,
            "observation": observation, "suggestion": suggestion}
    if basis == "code":
        item.update(path=path, start_line=line)
    else:
        item.update(reference=reference)
    return item


def review(verdict="approved", *, findings=(), responses=(), questions=(),
           summary="确定性审核结论"):
    return {"verdict": verdict, "summary": summary, "findings": list(findings),
            "responses": list(responses), "questions": list(questions)}


def response(finding_id, status="resolved", evidence_status="reproduced",
             observation="复读当前实现与登记检查"):
    return {"finding_id": finding_id, "status": status,
            "evidence_status": evidence_status, "observation": observation}


class Scripted:
    """按轮次和视角返回确定性审核报告；同时记录并发与载荷。"""

    def __init__(self, rounds, *, barrier_round=None):
        self.rounds, self.barrier_round = rounds, barrier_round
        self.calls, self.payloads, self.threads = [], [], []
        self.lock = threading.Lock()
        self.barrier = threading.Barrier(2) if barrier_round else None

    def reviewer(self, *, payload, perspective, **kwargs):
        number = payload["review"]["round"]
        with self.lock:
            self.calls.append((perspective, number))
            self.payloads.append((perspective, payload))
            self.threads.append(threading.current_thread().name)
        if self.barrier is not None and number == self.barrier_round:
            self.barrier.wait(timeout=10)
        return ReviewReport.model_validate(self.rounds[number - 1][perspective])

    def count(self, perspective=None, number=None):
        return sum(1 for item in self.calls
                   if (perspective is None or item[0] == perspective)
                   and (number is None or item[1] == number))


def start(task, settings, root, scripts, responses):
    original = runner.run_reviewer
    runner.run_reviewer = scripts.reviewer
    try:
        return workflow.start(task, settings, root, model=FakeModel(responses=responses))
    finally:
        runner.run_reviewer = original


def resume(run_id, settings, root, scripts, responses):
    original = runner.run_reviewer
    runner.run_reviewer = scripts.reviewer
    try:
        return workflow.resume(run_id, settings, root, model=FakeModel(responses=responses))
    finally:
        runner.run_reviewer = original


def implementation(value=2):
    return [turn("write_file", {"path": "app.py", "content": f"value = {value}\n"}), implement_done()]


def test_first_round_reads_one_snapshot_and_perspectives_are_independent(tmp_path):
    task, settings, root = setup(tmp_path)
    scripts = Scripted([
        {"correctness": review("changes_requested", findings=[finding()]),
         "context": review("changes_requested", findings=[finding(
             "缺少空输入验收", basis="requirement", reference="验收：空输入返回空结果",
             severity="major", evidence_status="source_inference")])},
        {"correctness": review(responses=[response("r1-f1"), response("r1-f2")]),
         "context": review(responses=[response("r1-f1"), response("r1-f2")])},
    ], barrier_round=1)
    result = start(task, settings, root, scripts,
                   [*implementation(), turn("write_file", {"path": "app.py", "content": "value = 3\n"}),
                    implement_fix_report()])
    assert result["status"] == "awaiting_approval", result
    assert result["review"]["review_rounds"] == 2
    assert result["review"]["rework_attempts"] == 1
    first = [payload for perspective, payload in scripts.payloads
             if payload["review"]["round"] == 1]
    assert len(first) == 2 and len({payload["review"]["basis_hash"] for payload in first}) == 1
    assert {payload["review"]["perspective"] for payload in first} == {"correctness", "context"}
    for payload in first:
        encoded = json.dumps(payload, ensure_ascii=False)
        assert "previous_findings" not in payload and "rework" not in payload
        assert "implementation_summary" not in encoded
    correctness, context = first
    assert "缺少空输入验收" not in json.dumps(correctness, ensure_ascii=False)
    assert "缺少空标题分支" not in json.dumps(context, ensure_ascii=False)
    assert len({thread for thread in scripts.threads[:2]}) == 2, "两个视角应并行执行"
    assert scripts.count(number=1) == 2
    round_one = result["review_rounds"][0]
    assert [item["id"] for item in round_one["findings"]] == ["r1-f1", "r1-f2"]
    assert {item["basis"] for item in round_one["findings"]} == {"code", "requirement"}
    assert all(item["sources"] for item in round_one["findings"])


def test_merge_deduplicates_same_issue_and_keeps_complementary_sources(tmp_path):
    task, settings, root = setup(tmp_path)
    same = finding()
    scripts = Scripted([
        {"correctness": review("changes_requested", findings=[
             same, finding("调用方缺少保护", path="app.py", line=4)]),
         "context": review("changes_requested", findings=[
             finding("缺少空标题分支", evidence_status="reproduced"),
             finding("缺少验收引用", basis="requirement", reference="验收：必须拒绝空标题")])},
        {"correctness": review(responses=[response("r1-f1"), response("r1-f2"),
                                          response("r1-f3")]),
         "context": review(responses=[response("r1-f1"), response("r1-f2"),
                                      response("r1-f3")])},
    ])
    result = start(task, settings, root, scripts,
                   [*implementation(), turn("write_file", {"path": "app.py", "content": "value = 3\n"}),
                    implement_fix_report()])
    round_one = result["review_rounds"][0]
    findings = round_one["findings"]
    assert len(findings) == 3, "同一问题合并，互补问题保留"
    merged = next(item for item in findings if item["observation"] == "缺少空标题分支")
    assert len(merged["sources"]) == 2
    assert {item["perspective"] for item in merged["sources"]} == {"correctness", "context"}
    assert merged["evidence_status"] == "reproduced", "证据取更强的一侧"
    assert len({item["key"] for item in findings}) == 3


def test_minor_preferences_do_not_block_approval(tmp_path):
    task, settings, root = setup(tmp_path)
    scripts = Scripted([{
        "correctness": review("changes_requested",
            findings=[finding("可以把变量名换得更短", severity="minor")]),
        "context": review("changes_requested",
            findings=[finding("注释风格可以更统一", severity="minor", line=2)])}])
    result = start(task, settings, root, scripts, implementation())
    assert result["status"] == "awaiting_approval", result
    assert result["review"]["verdict"] == "approved"
    assert result["review"]["non_blocking_findings"] == 2
    assert result["review"]["rework_attempts"] == 0
    report = JobStore(root).get(result["run_id"])["review_report"]
    assert "非阻断建议" in report["summary"]
    for item in report["findings"]:
        assert item["blocking"] is False


def test_unverified_severe_finding_cannot_be_approved(tmp_path):
    task, settings, root = setup(tmp_path)
    scripts = Scripted([{
        "correctness": review("changes_requested", findings=[finding(
            "疑似并发问题", severity="blocker", evidence_status="insufficient_information")],
            questions=["并发写入是否有保护？"]),
        "context": review("unable_to_review", questions=["并发写入是否有保护？"])}])
    result = start(task, settings, root, scripts, implementation())
    assert result["status"] == "needs_input", result
    assert result["review"]["verdict"] == "unable_to_review"
    assert result["review"]["rework_attempts"] == 0
    assert result["human_requests"][-1]["kind"] == "clarification"


def test_rework_limit_opens_handover_and_human_can_extend_with_trace(tmp_path):
    task, settings, root = setup(tmp_path, max_rework=1)
    scripts = Scripted([
        {"correctness": review("changes_requested", findings=[finding()]),
         "context": review("changes_requested", findings=[finding()])},
        {"correctness": review("changes_requested", responses=[response("r1-f1", "still_open")]),
         "context": review("changes_requested", responses=[response("r1-f1", "still_open")])},
        {"correctness": review(responses=[response("r1-f1")]),
         "context": review(responses=[response("r1-f1")])},
    ])
    result = start(task, settings, root, scripts,
                   [*implementation(), turn("write_file", {"path": "app.py", "content": "value = 3\n"}),
                    implement_fix_report()])
    assert result["status"] == "rework_limit_reached", result
    assert result["review"]["review_rounds"] == 2
    assert result["review"]["rework_attempts"] == 1
    handover = [item for item in result["human_requests"]
                if item["kind"] == "handover" and item["status"] == "pending"]
    assert handover and "已用满 1 次修改" in handover[0]["questions"][0]
    extended = gate.extend(result["run_id"], root, extra=1, reason="再核对一次边界")
    assert extended["status"] == "paused" and extended["rework_limit"] == 2
    state = JobStore(root).get(result["run_id"])
    assert state["rework_extensions"][0]["from"] == 1 and state["rework_extensions"][0]["to"] == 2
    assert [item["status"] for item in state["human_requests"]
            if item["kind"] == "handover"] == ["answered"]
    done = resume(result["run_id"], settings, root, scripts,
                  [turn("write_file", {"path": "app.py", "content": "value = 4\n"}),
                   implement_fix_report()])
    assert done["status"] == "awaiting_approval", done
    assert done["review"]["rework_attempts"] == 2
    assert done["review"]["review_rounds"] == 3
    assert done["review"]["rework_limit"] == 2


def test_same_commit_and_basis_reuses_review_without_second_model_round(tmp_path, monkeypatch):
    from repopilot.execution.workspace import head_commit
    task, settings, root = setup(tmp_path)
    scripts = Scripted([{"correctness": review(), "context": review()}])
    real_apply = outcome.apply

    def apply_then_interrupt(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(outcome, "apply", apply_then_interrupt)
    first = start(task, settings, root, scripts, implementation())
    assert first["status"] == "interrupted", first
    state = JobStore(root).get(first["run_id"])
    assert state["review_report"]["verdict"] == "approved"
    monkeypatch.setattr(outcome, "apply", real_apply)

    def no_review(**kwargs):
        raise AssertionError("同一提交和依据不应重新调用审核模型")
    monkeypatch.setattr(runner, "run_reviewer", no_review)
    workflow.recover(first["run_id"], root,
                     commit=head_commit(workflow.attach(state, root).path),
                     reason="核对审核现场后继续")
    resumed = workflow.resume(first["run_id"], settings, root, model=FakeModel(responses=[]))
    assert resumed["status"] == "awaiting_approval", resumed
    events = [json.loads(line) for line in
              (Path(resumed["output_directory"]) / "events.jsonl").read_text(
                  encoding="utf-8").splitlines()]
    assert any(item.get("event") == "review_reused" for item in events)
    assert resumed["model_calls"] == 2, "审核复用不增加模型调用"
    assert scripts.count() == 2, "第一轮的两份报告应被复用"


def dispute_scripts(*, c_outcome, comparison=None):
    def dispute_reviewer(*, payload, side, **kwargs):
        return DisputeReviewReport.model_validate({
            "judgement": "issue_confirmed" if side == "a" else "issue_refuted",
            "summary": f"{side} 独立复核",
            "evidence": [{"kind": "file", "path": "app.py", "start_line": 1,
                          "observation": f"{side} 读到的事实"}],
            "materials": [], "reasoning": f"{side} 的理由",
            "decisions": []})

    def arbitration(**kwargs):
        payload = kwargs["payload"]
        assert payload.get("reports"), "C 必须拿到 A／B 两份报告"
        return ArbitrationReport.model_validate({
            "outcome": c_outcome, "summary": "C 核对结论",
            "comparison": comparison if comparison is not None else [
                {"side": "a", "assessment": "supported", "observation": "A 的复现成立"},
                {"side": "b", "assessment": "contradicted", "observation": "B 的证据不成立"}],
            "verification": [], "reason": "C 比较了双方证据", "decisions": []})
    return dispute_reviewer, arbitration


def test_real_dispute_runs_independent_ab_then_arbitration_and_can_exclude(tmp_path, monkeypatch):
    from repopilot.review import outcome
    real_apply = outcome.apply
    task, settings, root = setup(tmp_path)
    scripts = Scripted([
        {"correctness": review("changes_requested", findings=[finding()]),
         "context": review("changes_requested", findings=[finding()])},
        {"correctness": review(responses=[response("r1-f1")]),
         "context": review("changes_requested",
                           responses=[response("r1-f1", "still_open")])},
    ])
    dispute_reviewer, arbitration = dispute_scripts(c_outcome="refuted")
    payloads = {}

    def recording_reviewer(*, payload, side, **kwargs):
        payloads[side] = json.loads(json.dumps(payload))
        return dispute_reviewer(payload=payload, side=side, **kwargs)
    monkeypatch.setattr(runner, "run_dispute_reviewer", recording_reviewer)
    monkeypatch.setattr(runner, "run_arbitration", arbitration)
    monkeypatch.setattr(outcome, "apply", real_apply)
    result = start(task, settings, root, scripts,
                   [*implementation(), turn("write_file", {"path": "app.py", "content": "value = 3\n"}),
                    implement_fix_report()])
    assert result["status"] == "awaiting_approval", result
    assert len(result["disputes"]) == 1
    record = result["disputes"][0]
    assert record["outcome"] == "refuted" and record["c"]["outcome"] == "refuted"
    assert set(payloads) == {"a", "b"}
    assert "reports" not in payloads["a"] and "reports" not in payloads["b"], "A／B首轮互看结论"
    invalid = [item for item in JobStore(root).get(result["run_id"])["review_report"]["findings"]
               if item["id"] == "r1-f1"]
    assert invalid and invalid[0]["status"] == "invalid"
    from repopilot.presentation.report import build
    state = JobStore(root).get(result["run_id"])
    html = build(state, root / "runs" / result["run_id"], Path.cwd())
    assert "视角结论" in html and "A／B＋C 争议复核" in html
    assert "问题不成立" in html and record["id"] in html


def test_review_basis_tracks_requirements_checks_and_invalidates_old_evidence(tmp_path):
    task, settings, root = setup(tmp_path)
    scripts = Scripted([{"correctness": review(), "context": review()}])
    result = start(task, settings, root, scripts, implementation())
    assert result["status"] == "awaiting_approval"
    state = JobStore(root).get(result["run_id"])
    original = review_basis.snapshot(state)
    assert original["commit"] == state["reviewed_commit"]
    assert original["verified_sha256"]
    changed = dict(state)
    changed["verified"] = [{"name": "unit", "exit_code": 0, "timed_out": False,
                            "truncated": False, "output_tail": "different"}]
    assert review_basis.snapshot(changed)["hash"] != original["hash"]
    changed = dict(state)
    changed["task_brief"] = dict(state["task_brief"], revision=1)
    assert review_basis.snapshot(changed)["hash"] != original["hash"]
    changed = dict(state)
    changed["reworks"] = 1
    assert review_basis.snapshot(changed)["hash"] != original["hash"]
    review_basis.invalidate(state, "human_clarification")
    assert state["review_report"] is None and state["approval"] is None
    assert state["verified"] == [] and state["review_basis"] is None
    assert state["review_invalidations"][-1]["reason"] == "human_clarification"
    assert state["review_rounds"], "历史审核记录不能因失效而删除"


def test_undetermined_dispute_waits_for_human_and_cannot_auto_approve(tmp_path, monkeypatch):
    from repopilot.review import outcome
    task, settings, root = setup(tmp_path)
    scripts = Scripted([
        {"correctness": review("changes_requested", findings=[finding()]),
         "context": review("changes_requested", findings=[finding()])},
        {"correctness": review(responses=[response("r1-f1")]),
         "context": review("changes_requested",
                           responses=[response("r1-f1", "still_open")])},
    ])
    dispute_reviewer, arbitration = dispute_scripts(c_outcome="undetermined", comparison=[])
    monkeypatch.setattr(runner, "run_dispute_reviewer", dispute_reviewer)
    monkeypatch.setattr(runner, "run_arbitration", arbitration)
    result = start(task, settings, root, scripts,
                   [*implementation(), turn("write_file", {"path": "app.py", "content": "value = 3\n"}),
                    implement_fix_report()])
    assert result["status"] == "needs_input", result
    assert result["stop_reason"] == "review_dispute_requires_decision"
    assert result["review"]["verdict"] == "undetermined"
    request = [item for item in result["human_requests"]
               if item["kind"] == "dispute" and item["status"] == "pending"][0]
    with pytest.raises(StateError, match="use_review_decide"):
        human.answer(result["run_id"], root, request_id=request["id"],
                     version=request["version"], text="我判断成立")
    decided = gate.decide(result["run_id"], root, dispute_id=result["disputes"][0]["id"],
                          decision="refuted", reason="核对源码后认为该发现不成立")
    assert decided["status"] == "awaiting_approval", decided
    state = JobStore(root).get(result["run_id"])
    assert state["disputes"][0]["outcome"] == "human_refuted"
    assert state["review_report"]["verdict"] == "approved"
    assert [item["status"] for item in state["human_requests"]
            if item["kind"] == "dispute"] == ["answered"]
    assert merge.approval_problems(state) == [], "争议快照必须同步为人工结论"
    with pytest.raises(StateError):
        gate.decide(result["run_id"], root, dispute_id=state["disputes"][0]["id"],
                    decision="refuted", reason="重复判断")


def test_agreement_without_evidence_does_not_close_the_finding(tmp_path):
    task, settings, root = setup(tmp_path)
    scripts = Scripted([
        {"correctness": review("changes_requested", findings=[finding()]),
         "context": review("changes_requested", findings=[finding()])},
        {"correctness": review(responses=[response("r1-f1", evidence_status="insufficient_information")]),
         "context": review(responses=[response("r1-f1", evidence_status="insufficient_information")])},
    ])
    result = start(task, settings, root, scripts,
                   [*implementation(), turn("write_file", {"path": "app.py", "content": "value = 3\n"}),
                    implement_fix_report()])
    assert result["status"] == "review_inconclusive", result
    finding_state = JobStore(root).get(result["run_id"])["review_report"]["findings"][0]
    assert finding_state["status"] == "unverified"
    assert result["review"]["rework_attempts"] == 1


def test_review_show_needs_no_credentials_and_reports_evidence(tmp_path, monkeypatch):
    task, settings, root = setup(tmp_path)
    scripts = Scripted([{
        "correctness": review(), "context": review()}])
    result = start(task, settings, root, scripts, implementation())
    assert result["status"] == "awaiting_approval"
    monkeypatch.setattr(cli, "load_settings", lambda *_: pytest.fail("查看审核不能加载模型凭证"))
    args = cli.build_parser().parse_args(["review", "show", result["run_id"], "--output", str(root)])
    shown = cli.execute_command(args)
    assert shown["status"] == "ok"
    assert shown["review"]["verdict"] == "approved"
    assert shown["review"]["rework_limit"] == settings.max_rework
    assert len(shown["review"]["rounds"]) == 1
    assert shown["review"]["rounds"][0]["perspectives"] == {
        "correctness": "approved", "context": "approved"}


def test_legacy_run_without_review_fields_resumes_with_defaults(tmp_path):
    task, settings, root = setup(tmp_path)
    state = workflow.create_job(task, settings, root)
    for key in ("review_rounds", "disputes", "review_reuse", "review_invalidations",
                "review_pending", "review_draft", "rework_extensions",
                "review_dispute_decisions", "review_basis", "rework_limit"):
        state.pop(key, None)
    JobStore(root).save(state)
    scripts = Scripted([{"correctness": review(), "context": review()}])
    resumed = resume(state["run_id"], settings, root, scripts, implementation())
    assert resumed["status"] == "awaiting_approval", resumed
    assert resumed["review"]["rework_limit"] == settings.max_rework
    assert resumed["review_rounds"], "旧运行补默认字段后仍能完成双审"
