"""人工等待、独立进程答复、版本约束与既有审批／现场恢复的真实本地验证。"""

import json
from pathlib import Path
import subprocess
import sys

import pytest

import repopilot.cli as cli
from repopilot.agents import loop as engine
from repopilot.orchestration import human, workflow
from repopilot.orchestration.state import approval_digest, attach
from repopilot.presentation.report import build
from repopilot.storage.jobs import JobStore, StateError
from tests.runtime.test_review_loop import (
    FakeModel, implement_done, review_approved, review_changes, setup, turn,
)


def clarification(summary="需要确定空输入行为", questions=None):
    return turn("ImplementationReport", {"status": "needs_clarification", "summary": summary,
        "evidence": [], "questions": ["空输入应报错还是返回空结果？"] if questions is None else questions,
        "next_steps": []})


def write(value=2):
    return turn("write_file", {"path": "app.py", "content": f"value = {value}\n"})


def waiting(tmp_path, **settings_overrides):
    task, settings, root = setup(tmp_path, **settings_overrides)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[write(), clarification()]))
    assert result["status"] == "needs_input", result
    return result, settings, root


def reply(result, root, text="空输入返回空结果"):
    request = next(r for r in human.list_requests(result["run_id"], root)["requests"]
                   if r["kind"] == "clarification" and r["status"] == "pending")
    return human.answer(result["run_id"], root, request_id=request["id"],
                        version=request["version"], text=text)


def test_partial_implementation_waits_without_replaying_and_resumes_with_answer(tmp_path, monkeypatch):
    first, settings, root = waiting(tmp_path)
    run_id = first["run_id"]
    before = JobStore(root).get(run_id)
    assert before["in_flight"] is None and before["phase"] == "implement"
    workspace = attach(before, root).path
    assert (workspace / "app.py").read_text() == "value = 2\n"
    assert first["repository_unchanged"] is True
    monkeypatch.setattr(engine, "make_model", lambda *_: pytest.fail("等待期间不应构造模型"))
    again = workflow.resume(run_id, settings, root)
    after = JobStore(root).get(run_id)
    assert again["status"] == "needs_input" and again["model_calls"] == 2
    assert after["active_seconds"] == before["active_seconds"]
    assert after["control"] == before["control"] and after["edits"] == before["edits"]
    assert after["human_requests"] == before["human_requests"]

    answered = reply(first, root)
    assert answered["status"] == "paused"
    assert JobStore(root).get(run_id)["approval"] is None
    payloads = []
    original = engine.run_phase
    def record(**kwargs):
        payloads.append(kwargs["payload"])
        return original(**kwargs)
    monkeypatch.setattr(engine, "run_phase", record)
    resumed = workflow.resume(run_id, settings, root,
        model=FakeModel(responses=[write(3), implement_done(), review_approved()]))
    assert resumed["status"] == "awaiting_approval", resumed
    assert resumed["model_calls"] == 5 and resumed["edits"]["edits"] == 2
    assert resumed["review"]["rework_attempts"] == 0
    assert resumed["requirements_revision"] == 1
    assert all(p["human_answers"][0]["answer"] == "空输入返回空结果" for p in payloads)
    assert resumed["cost"]["accounted_cny"] > first["cost"]["accounted_cny"]


def test_answer_is_idempotent_but_conflicting_answer_is_rejected(tmp_path):
    first, _, root = waiting(tmp_path)
    answered = reply(first, root)
    request = answered["request"]
    before = JobStore(root).get(first["run_id"])
    duplicate = human.answer(first["run_id"], root, request_id=request["id"],
                             version=request["version"], text="空输入返回空结果")
    assert duplicate == answered
    assert JobStore(root).get(first["run_id"]) == before
    with pytest.raises(StateError, match="already_answered"):
        human.answer(first["run_id"], root, request_id=request["id"],
                     version=request["version"], text="改为报错")


@pytest.mark.parametrize("change", ["workspace", "task", "issue", "answers", "phase", "in_flight"])
def test_answer_rejects_changed_context_without_losing_pending_request(tmp_path, change):
    first, _, root = waiting(tmp_path)
    store = JobStore(root)
    state = store.get(first["run_id"])
    if change == "workspace":
        (attach(state, root).path / "app.py").write_text("value = 99\n")
    elif change == "task":
        state["task"]["request"] += " changed"
    elif change == "issue":
        state["issue"] = {"title": "changed"}
    elif change == "answers":
        state["human_answers"] = [{"answer": "changed"}]
    elif change == "phase":
        state["phase"] = "rework"
    else:
        state["in_flight"] = "implement"
    store.save(state)
    with pytest.raises(StateError, match="stale_human_request"):
        reply(first, root)
    after = store.get(first["run_id"])
    assert after["requirements_revision"] == 0
    assert after["human_requests"][0]["status"] == "pending"


def test_wrong_version_unknown_request_and_empty_reply_do_not_advance(tmp_path):
    first, _, root = waiting(tmp_path)
    request = first["human_requests"][0]
    with pytest.raises(StateError, match="version_mismatch"):
        human.answer(first["run_id"], root, request_id=request["id"], version="stale", text="yes")
    with pytest.raises(StateError, match="request_not_found"):
        human.answer(first["run_id"], root, request_id="unknown", version=request["version"], text="yes")
    with pytest.raises(StateError, match="reply_required"):
        reply(first, root, "  ")
    assert JobStore(root).get(first["run_id"])["status"] == "needs_input"


def test_identical_tasks_have_distinct_request_identities(tmp_path):
    first, settings, root = waiting(tmp_path)
    task = JobStore(root).get(first["run_id"])["task"]
    from repopilot.core.config import Task
    second = workflow.start(Task.model_validate(task), settings, root,
        model=FakeModel(responses=[write(), clarification()]))
    first_request, second_request = first["human_requests"][0], second["human_requests"][0]
    assert first_request["id"] != second_request["id"]
    assert first_request["version"] != second_request["version"]
    with pytest.raises(StateError, match="request_not_found"):
        human.answer(second["run_id"], root, request_id=first_request["id"],
                     version=first_request["version"], text="first run only")


def test_review_question_reverifies_and_does_not_consume_extra_rework_round(tmp_path):
    task, settings, root = setup(tmp_path, max_rework=1)
    question = turn("ReviewReport", {"verdict": "unable_to_review", "summary": "需要确认行为",
                                    "findings": [], "questions": ["空输入如何处理？"]})
    first = workflow.start(task, settings, root,
        model=FakeModel(responses=[write(), implement_done(), question]))
    assert first["status"] == "needs_input" and first["phase"] == "review"
    reply(first, root)
    assert JobStore(root).get(first["run_id"])["phase"] == "verify"
    assert JobStore(root).get(first["run_id"])["verified"] == []
    done = workflow.resume(first["run_id"], settings, root,
        model=FakeModel(responses=[review_changes(), write(3), implement_done(), review_approved()]))
    assert done["status"] == "awaiting_approval", done
    assert done["review"]["rework_attempts"] == 1
    assert done["review"]["review_rounds"] == 3  # 澄清一次、正式初审、一次返工复审。
    verified = JobStore(root).get(first["run_id"])["verified"]
    assert verified and all(check["exit_code"] == 0 for check in verified)


def test_clarification_inside_rework_does_not_increment_attempt_again(tmp_path):
    task, settings, root = setup(tmp_path, max_rework=1)
    first = workflow.start(task, settings, root,
        model=FakeModel(responses=[write(), implement_done(), review_changes(), clarification()]))
    assert first["status"] == "needs_input" and first["phase"] == "rework"
    assert first["review"]["rework_attempts"] == 1
    reply(first, root)
    done = workflow.resume(first["run_id"], settings, root,
        model=FakeModel(responses=[write(3), implement_done(), review_approved()]))
    assert done["status"] == "awaiting_approval", done
    assert done["review"]["rework_attempts"] == 1


def test_business_answer_cannot_approve_or_recover(tmp_path):
    task, settings, root = setup(tmp_path)
    result = workflow.start(task, settings, root,
        model=FakeModel(responses=[write(), implement_done(), review_approved()]))
    request = result["human_requests"][0]
    assert request["kind"] == "approval"
    with pytest.raises(StateError, match="use_approve_reject_or_recover"):
        human.answer(result["run_id"], root, request_id=request["id"],
                     version=request["version"], text="同意")
    assert JobStore(root).get(result["run_id"])["status"] == "awaiting_approval"
    workflow.decide(result["run_id"], root, commit=result["review"]["reviewed_commit"],
                    approved=False, reason="暂不交付")
    answered = human.list_requests(result["run_id"], root)["requests"][0]
    assert answered["status"] == "answered" and answered["reply"]["action"] == "reject"


def test_changed_human_answer_invalidates_approval_digest(tmp_path):
    first, settings, root = waiting(tmp_path)
    reply(first, root)
    done = workflow.resume(first["run_id"], settings, root,
        model=FakeModel(responses=[implement_done(), review_approved()]))
    store = JobStore(root)
    state = store.get(first["run_id"])
    assert done["status"] == "awaiting_approval"
    assert state["approval_digest"] == approval_digest(state)
    state["human_answers"][0]["answer"] = "different requirement"
    store.save(state)
    with pytest.raises(StateError, match="stale_approval"):
        workflow.decide(first["run_id"], root, commit=done["review"]["reviewed_commit"],
                        approved=True, reason="确认")


def test_legacy_approval_and_new_request_are_saved_together(tmp_path):
    task, settings, root = setup(tmp_path)
    done = workflow.start(task, settings, root,
        model=FakeModel(responses=[write(), implement_done(), review_approved()]))
    store = JobStore(root)
    state = store.get(done["run_id"])
    for name in ("human_requests", "human_answers", "requirements_revision"):
        state.pop(name)
    store.save(state)
    assert human.list_requests(done["run_id"], root)["requests"] == []
    assert approval_digest(state) == state["approval_digest"]
    workflow.decide(done["run_id"], root, commit=done["review"]["reviewed_commit"],
                    approved=True, reason="兼容旧运行")
    saved = store.get(done["run_id"])
    assert saved["approval"]["approved"] is True
    assert saved["human_requests"][0]["reply"]["action"] == "approve"


def test_interrupted_phase_uses_same_requests_and_explicit_recovery(tmp_path):
    task, settings, root = setup(tmp_path)
    first = workflow.start(task, settings, root, model=FakeModel(responses=[]), stop_after="prepare")
    store = JobStore(root)
    state = store.get(first["run_id"])
    state["in_flight"] = "implement"
    store.save(state)
    result = workflow.resume(first["run_id"], settings, root, model=FakeModel(responses=[]))
    assert result["status"] == "recovery_required"
    request = result["human_requests"][0]
    assert request["kind"] == "recovery"
    again = workflow.resume(first["run_id"], settings, root, model=FakeModel(responses=[]))
    assert again["human_requests"] == result["human_requests"]
    with pytest.raises(StateError, match="use_approve_reject_or_recover"):
        human.answer(first["run_id"], root, request_id=request["id"], version=request["version"], text="yes")
    with pytest.raises(StateError, match="recovery_commit_mismatch"):
        workflow.recover(first["run_id"], root, commit="0" * 40, reason="核对")
    workflow.recover(first["run_id"], root, commit=state["base_commit"], reason="现场无额外变化")
    saved = store.get(first["run_id"])
    assert saved["human_requests"][0]["reply"]["action"] == "recover"
    assert saved["status"] == "paused" and saved["in_flight"] is None


def test_explicit_retry_keeps_confirmed_answers_and_their_original_run(tmp_path):
    first, settings, root = waiting(tmp_path, max_model_calls=3)
    reply(first, root)
    limited = workflow.resume(first["run_id"], settings, root, model=FakeModel(responses=[write(3)]))
    assert limited["status"] == "limit_reached", limited
    retried = workflow.retry(first["run_id"], settings, root, reason="给足后续额度",
        overrides={"max_model_calls": 6}, model=FakeModel(responses=[write(3), implement_done(), review_approved()]))
    assert retried["status"] == "awaiting_approval", retried
    saved = JobStore(root).get(retried["run_id"])
    assert saved["human_answers"][0]["run_id"] == first["run_id"]
    assert saved["requirements_revision"] == 1
    assert retried["budget"]["calls"] > retried["cost"]["calls"]


def test_cli_list_answer_in_new_process_and_duplicate_answer_need_no_credentials(tmp_path, monkeypatch):
    first, _, root = waiting(tmp_path)
    def unavailable(*_, **__):
        pytest.fail("本地人工交互不得加载模型或平台凭证")
    monkeypatch.setattr(cli, "load_settings", unavailable)
    monkeypatch.setattr(cli, "open_forge", unavailable)
    args = cli.build_parser().parse_args(["human", "list", first["run_id"], "--output", str(root)])
    assert cli.execute_command(args)["requests"]
    request = first["human_requests"][0]
    command = [sys.executable, "-m", "repopilot.cli", "human", "answer", first["run_id"],
        request["id"], "--version", request["version"], "--answer", "return empty",
        "--output", str(root)]
    for _ in range(2):
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["status"] == "paused"
    saved = JobStore(root).get(first["run_id"])
    assert len(saved["human_answers"]) == 1 and saved["control"]["model_calls"] == 2
    answer_args = cli.build_parser().parse_args(command[3:])
    assert cli.execute_command(answer_args)["status"] == "paused"


def test_request_redaction_and_report_escape(tmp_path):
    task, settings, root = setup(tmp_path)
    first = workflow.start(task, settings, root,
        model=FakeModel(responses=[clarification(summary="synthetic-private-key",
                                               questions=["<script>alert(1)</script>"])]))
    run_id = first["run_id"]
    state = JobStore(root).get(run_id)
    assert "synthetic-private-key" not in json.dumps(state)
    html = build(state, root / "runs" / run_id, Path.cwd())
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "repopilot human list" in html


def test_no_question_text_uses_summary_as_a_concrete_request(tmp_path):
    task, settings, root = setup(tmp_path)
    first = workflow.start(task, settings, root, model=FakeModel(responses=[clarification(questions=[])]))
    assert first["human_requests"][0]["questions"] == ["需要确定空输入行为"]
