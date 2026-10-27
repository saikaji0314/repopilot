import json
import subprocess
from uuid import uuid4

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from repopilot.agents.loop import investigate
from repopilot.core.config import Check, RuntimeSettings, Task


class FakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def turn(name, args, *, usage=True):
    return AIMessage(content="", tool_calls=[{"id": uuid4().hex, "name": name, "args": args}],
                     usage_metadata={"input_tokens": 80, "output_tokens": 30, "total_tokens": 110}
                     if usage else None)


def file_evidence(observation="改了空标题分支"):
    return {"kind": "file", "path": "app.py", "start_line": 1, "observation": observation}


def check_evidence():
    return {"kind": "check", "name": "unit", "exit_code": 0, "observation": "登记检查通过"}


def implement_done():
    return turn("ImplementationReport", {"status": "implemented", "summary": "已修复空标题",
                                        "evidence": [file_evidence(), check_evidence()],
                                        "next_steps": [], "questions": []})


def implement_fix(path):
    """返工：先真正改文件，再给出实现报告。"""

    return [turn("write_file", {"path": "app.py", "content": "value = 3\n"}), implement_fix_report()]


def implement_fix_report():
    return turn("ImplementationReport", {"status": "implemented", "summary": "按审核意见补了用例",
                                        "evidence": [file_evidence("补了边界用例"), check_evidence()],
                                        "next_steps": [], "questions": []})


def review_changes():
    return turn("ReviewReport", {
        "verdict": "changes_requested", "summary": "缺少边界用例",
        "findings": [{"path": "app.py", "start_line": 1, "severity": "major",
                      "observation": "只改了实现，没有覆盖空标题", "suggestion": "补一条空标题断言"}],
        "questions": []})


def review_approved():
    return turn("ReviewReport", {"verdict": "approved", "summary": "改动满足工单且检查通过",
                                "findings": [], "questions": []})


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "synthetic@example.test")
    git("config", "user.name", "synthetic")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    return repo


def setup(tmp_path, **overrides):
    repo = make_repo(tmp_path)
    task = Task(id="review", request="修改 app.py", repository=repo,
                checks={"unit": Check(argv=["{python}", "-c", "print('checked')"])})
    return task, RuntimeSettings(api_key="synthetic-private-key", **overrides), tmp_path / "runtime"


def write_change(path, value):
    def action():
        (path / "app.py").write_text(value, encoding="utf-8")
        return {"ok": True, "path": "app.py"}
    return action


def events(run_dir):
    return [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]


def run_dir_of(output):
    return next((output / "runs").iterdir())


def test_review_anchors_on_a_commit_and_records_approval(tmp_path):
    task, settings, output = setup(tmp_path)
    model = FakeModel(responses=[turn("write_file", {"path": "app.py", "content": "value = 2\n"}),
                                 implement_done(), turn("read_file", {"path": "app.py"}),
                                 review_approved()])
    summary = investigate(task, settings, output, model=model)
    assert summary["status"] == "completed", summary
    assert summary["review"]["verdict"] == "approved"
    assert summary["review"]["review_rounds"] == 1
    assert summary["review"]["rework_attempts"] == 0
    assert summary["review"]["reviewed_commit"] == summary["review"]["final_commit"]
    assert summary["review"]["reviewed_commit"] != summary["changes"]["base_commit"]
    assert summary["repository_unchanged"] is True
    run_dir = run_dir_of(output)
    assert json.loads((run_dir / "review.json").read_text(encoding="utf-8"))["verdict"] == "approved"
    assert (run_dir / "review-history.json").exists()
    phases = [(item["event"], item.get("phase")) for item in events(run_dir)
              if item["event"] in {"tool_started", "review_started", "review_finished"}]
    assert ("review_started", None) in phases
    assert ("tool_started", "review") in phases


def test_review_findings_trigger_one_rework_then_approval(tmp_path):
    task, settings, output = setup(tmp_path)
    model = FakeModel(responses=[turn("write_file", {"path": "app.py", "content": "value = 2\n"}),
                                 implement_done(), review_changes(),
                                 *implement_fix(tmp_path), review_approved()])
    summary = investigate(task, settings, output, model=model)
    assert summary["status"] == "completed", summary
    assert summary["review"]["verdict"] == "approved"
    assert summary["review"]["review_rounds"] == 2, "一次返工意味着两次审核"
    assert summary["review"]["rework_attempts"] == 1
    history = json.loads((run_dir_of(output) / "review-history.json").read_text(encoding="utf-8"))
    assert [item["verdict"] for item in history] == ["changes_requested", "approved"]
    assert history[0]["commit"] != history[1]["commit"], "返工改了文件，审核锚点必须前进"
    assert history[1]["commit"] == summary["changes"]["head_commit"]
    rework_started = [item for item in events(run_dir_of(output)) if item["event"] == "rework_started"]
    assert rework_started and rework_started[0]["findings"] == 1


def test_rework_that_changes_nothing_keeps_the_same_review_anchor(tmp_path):
    """返工没有产生新提交时，审核锚点不变；这时第二次审核看到的是同一版本。"""

    task, settings, output = setup(tmp_path)
    model = FakeModel(responses=[turn("write_file", {"path": "app.py", "content": "value = 2\n"}),
                                 implement_done(), review_changes(),
                                 implement_fix_report(), review_approved()])
    summary = investigate(task, settings, output, model=model)
    history = json.loads((run_dir_of(output) / "review-history.json").read_text(encoding="utf-8"))
    assert history[0]["commit"] == history[1]["commit"]
    assert summary["review"]["review_rounds"] == 2


def test_rework_stops_at_the_limit_and_marks_the_run_for_humans(tmp_path):
    task, settings, output = setup(tmp_path, max_rework=1)
    model = FakeModel(responses=[turn("write_file", {"path": "app.py", "content": "value = 2\n"}),
                                 implement_done(), review_changes(),
                                 *implement_fix(tmp_path), review_changes()])
    summary = investigate(task, settings, output, model=model)
    assert summary["status"] == "rework_limit_reached", summary
    assert summary["stop_reason"] == "max_rework"
    assert summary["review"]["verdict"] == "changes_requested"
    assert summary["review"]["review_rounds"] == 2
    assert summary["review"]["rework_attempts"] == 1
    assert summary["review"]["limit"] == 1


def test_reviewer_only_gets_read_only_tools(tmp_path):
    task, settings, output = setup(tmp_path)
    model = FakeModel(responses=[turn("write_file", {"path": "app.py", "content": "value = 2\n"}),
                                 implement_done(), turn("read_file", {"path": "app.py"}),
                                 review_approved()])
    investigate(task, settings, output, model=model)
    used = {}
    for item in events(run_dir_of(output)):
        if item["event"] == "tool_started":
            used.setdefault(item["phase"], set()).add(item["name"])
    assert used["implement"] | {"write_file"} == used["implement"]
    assert used["review"] == {"read_file"}, "审核阶段不得使用编辑工具"
    assert "write_file" not in used["review"] and "replace_in_file" not in used["review"]


def test_review_is_skipped_when_nothing_changed(tmp_path):
    task, settings, output = setup(tmp_path)
    model = FakeModel(responses=[turn("read_file", {"path": "app.py"}),
                                 turn("ImplementationReport", {"status": "blocked", "summary": "无法完成",
                                                               "evidence": [], "next_steps": [],
                                                               "questions": ["需求不明确"]})])
    summary = investigate(task, settings, output, model=model)
    assert summary["status"] == "completed", summary
    assert summary["review"]["verdict"] is None
    assert summary["review"]["review_rounds"] == 0
    assert summary["stop_reason"] == "report"
    assert [item["reason"] for item in events(run_dir_of(output)) if item["event"] == "review_skipped"] \
        == ["implementer_status_blocked"], "实现方已声明阻塞，不应再进入审核"


def test_review_is_skipped_when_the_implementation_claims_but_changes_nothing(tmp_path):
    task, settings, output = setup(tmp_path)
    model = FakeModel(responses=[turn("read_file", {"path": "app.py"}),
                                 turn("ImplementationReport", {"status": "implemented", "summary": "声称完成",
                                                               "evidence": [file_evidence(), check_evidence()],
                                                               "next_steps": [], "questions": []})])
    summary = investigate(task, settings, output, model=model)
    assert summary["status"] == "completed", summary
    assert summary["report_status"] == "blocked", "没有文件变更就不能留下 implemented"
    assert [item["reason"] for item in events(run_dir_of(output)) if item["event"] == "review_skipped"] \
        == ["implementer_status_blocked"]


def test_review_can_be_turned_off(tmp_path):
    task, settings, output = setup(tmp_path)
    model = FakeModel(responses=[turn("write_file", {"path": "app.py", "content": "value = 2\n"}),
                                 implement_done()])
    summary = investigate(task, settings, output, model=model, review=False)
    assert summary["status"] == "completed", summary
    assert summary["review"]["enabled"] is False
    assert summary["review"]["verdict"] is None
    assert summary["report_status"] == "implemented"


def test_unable_to_review_with_questions_waits_for_input(tmp_path):
    task, settings, output = setup(tmp_path)
    model = FakeModel(responses=[turn("write_file", {"path": "app.py", "content": "value = 2\n"}),
                                 implement_done(),
                                 turn("ReviewReport", {"verdict": "unable_to_review",
                                                       "summary": "登记检查缺失，无法判断",
                                                       "findings": [], "questions": ["lint 检查指向哪个文件？"]})])
    summary = investigate(task, settings, output, model=model)
    assert summary["status"] == "needs_input", summary
    assert summary["stop_reason"] == "human_clarification_required"
    assert summary["human_requests"][0]["questions"] == ["lint 检查指向哪个文件？"]
    assert summary["review"]["verdict"] == "unable_to_review"
    assert summary["review"]["rework_attempts"] == 0


def test_verified_checks_are_independently_rerun_before_review(tmp_path):
    task, settings, output = setup(tmp_path)
    model = FakeModel(responses=[turn("write_file", {"path": "app.py", "content": "value = 2\n"}),
                                 implement_done(), turn("read_file", {"path": "app.py"}),
                                 review_approved()])
    investigate(task, settings, output, model=model)
    run_dir = run_dir_of(output)
    verified = json.loads((run_dir / "verification.json").read_text(encoding="utf-8"))
    assert verified == [{"name": "unit", "exit_code": 0, "timed_out": False, "truncated": False,
                         "output_tail": "checked"}]
    phases = [item["event"] for item in events(run_dir)]
    assert "verify_finished" in phases
    assert phases.index("verify_finished") < phases.index("review_finished"), "审核结论必须在独立复核之后"


def test_rework_that_reports_not_implemented_stops_without_another_review(tmp_path):
    """返工自己判定没做完时，不应再对着未完成版本跑下一轮审核。"""

    task, settings, output = setup(tmp_path, max_rework=1)
    model = FakeModel(responses=[turn("write_file", {"path": "app.py", "content": "value = 2\n"}),
                                 implement_done(), review_changes(),
                                 turn("ImplementationReport", {"status": "blocked", "summary": "改不动",
                                                               "evidence": [], "next_steps": [],
                                                               "questions": ["需求需要澄清"]})])
    summary = investigate(task, settings, output, model=model)
    assert summary["status"] == "phase_error", summary
    assert summary["stop_reason"] == "rework_status_blocked"
    assert summary["review"]["review_rounds"] == 1, "不应再进入第二轮审核"
    assert summary["review"]["rework_attempts"] == 1
    assert summary["review"]["verdict"] == "changes_requested"
    skipped = [item["reason"] for item in events(run_dir_of(output)) if item["event"] == "review_skipped"]
    assert skipped == ["rework_status_blocked"]


def test_malformed_rework_report_is_recorded_instead_of_crashing(tmp_path):
    task, settings, output = setup(tmp_path)
    bad = turn("ImplementationReport", {"status": "finished", "summary": "非法状态"})
    model = FakeModel(responses=[turn("write_file", {"path": "app.py", "content": "value = 2\n"}),
                                 implement_done(), review_changes(), bad])
    summary = investigate(task, settings, output, model=model)
    assert summary["status"] == "phase_error", summary
    assert summary["stop_reason"] == "invalid_rework_report"
    assert summary["review"]["verdict"] == "changes_requested", "已拿到的审核结论要保留"
    assert summary["repository_unchanged"] is True
    errors = [item for item in events(run_dir_of(output)) if item["event"] == "run_error"]
    assert errors and errors[-1]["phase"] == "rework"


def test_malformed_review_report_is_recorded_as_inconclusive(tmp_path):
    task, settings, output = setup(tmp_path)
    bad = turn("ReviewReport", {"verdict": "maybe", "summary": "非法结论"})
    model = FakeModel(responses=[turn("write_file", {"path": "app.py", "content": "value = 2\n"}),
                                 implement_done(), bad])
    summary = investigate(task, settings, output, model=model)
    assert summary["status"] == "review_inconclusive", summary
    assert summary["stop_reason"] == "invalid_review_report"
    assert summary["review"]["verdict"] is None
    assert summary["review"]["reviewed_commit"], "审核锚点仍要保留，便于人工核对"
    errors = [item for item in events(run_dir_of(output)) if item["event"] == "run_error"]
    assert errors and errors[-1]["phase"] == "review"


def test_model_cannot_approve_a_failed_independent_check(tmp_path):
    task, settings, output = setup(tmp_path)
    task = task.model_copy(update={"checks": {"unit": Check(argv=["{python}", "-c", "raise SystemExit(1)"])}})
    model = FakeModel(responses=[turn("write_file", {"path": "app.py", "content": "value = 2\n"}),
                                 implement_done(), review_approved()])
    summary = investigate(task, settings, output, model=model)
    assert summary["status"] == "review_inconclusive", summary
    assert summary["review"]["verdict"] != "approved"
