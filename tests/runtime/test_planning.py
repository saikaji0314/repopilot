"""M3计划、集中提问与子Issue映射的本地机制验证；模型与平台均为确定性替身。"""

import json
from pathlib import Path

import pytest

import repopilot.cli as cli
from repopilot.collaboration import comments
from repopilot.collaboration.requests import HumanRequest
from repopilot.agents import loop as engine
from repopilot.core.config import RuntimeSettings
from repopilot.delivery import watcher
from repopilot.orchestration import human, workflow
from repopilot.planning import mapping, planner
from repopilot.planning.contracts import PlanFault
from repopilot.presentation.formatting import next_action
from repopilot.presentation.report import build
from repopilot.storage.jobs import JobStore, StateError
from tests.runtime.test_human import reply
from tests.runtime.test_review_loop import FakeModel, implement_done, review_approved, setup, turn


ISSUE = {"repository": "owner/repo", "number": 1, "url": "https://github.com/owner/repo/issues/1",
         "title": "需求", "body": "实现需求"}


def subtask(identifier="s1", **overrides):
    value = {"id": identifier, "title": f"子任务 {identifier}", "goal": "完成目标",
             "inputs": ["app.py"], "outputs": ["修改后的 app.py"], "acceptance": ["登记检查通过"],
             "depends_on": [], "boundaries": ["app.py"], "interfaces": [],
             "track": "none", "track_reason": ""}
    value.update(overrides)
    return value


def brief(**overrides):
    value = {"goal": "修改 app.py 的取值", "acceptance": ["value 变为 2"],
             "assumptions": [], "out_of_scope": [], "unresolved": [], "sources": ["app.py"]}
    value.update(overrides)
    return value


def plan_ready(subtasks=None, *, split="none", **overrides):
    value = {"status": "ready", "summary": "整理完成", "split": split,
             "split_reason": "按依赖顺序执行", "brief": brief(),
             "subtasks": [subtask()] if subtasks is None else subtasks,
             "questions": [], "decisions": []}
    value.update(overrides)
    return turn("PlanReport", value)


def plan_question(text, **overrides):
    value = {"status": "needs_clarification", "summary": "需要确认业务行为",
             "split": "none", "split_reason": "信息不足", "brief": None, "subtasks": [],
             "questions": [text], "decisions": []}
    value.update(overrides)
    return turn("PlanReport", value)


def write(value=2):
    return turn("write_file", {"path": "app.py", "content": f"value = {value}\n"})


def pending(result):
    return next(item for item in result["human_requests"] if item["status"] == "pending")


def run_plan_until_here(tmp_path, *, plan_subtasks=None, split="none", issue=None):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    result = workflow.start(task, settings, root, issue=issue,
        model=FakeModel(responses=[plan_ready(plan_subtasks, split=split)]), stop_after="plan")
    return task, settings, root, result


def test_clear_small_task_needs_no_question_and_reaches_approval(tmp_path, monkeypatch):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    seen, original = {}, engine.run_phase
    def capture(**kwargs):
        seen[kwargs["phase"]] = kwargs["payload"]
        return original(**kwargs)
    monkeypatch.setattr(engine, "run_phase", capture)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[
        plan_ready(), write(), implement_done(), review_approved()]))
    assert result["status"] == "awaiting_approval", result
    assert result["plan"]["split"] == "none" and len(result["plan"]["subtasks"]) == 1
    assert result["plan_status"]["state"] == "completed"
    assert result["question_batches"] == []
    assert result["task_brief"]["revision"] == 1
    assert result["model_calls"] == 4
    assert seen["implement"]["plan"]["version"] == result["plan"]["version"]
    assert seen["implement"]["task_brief"]["revision"] == 1
    assert seen["review"]["plan"]["subtasks"][0]["result"]["status"] == "completed"


def test_plan_question_counts_once_and_answer_updates_brief_then_continues(tmp_path, monkeypatch):
    from tests.runtime.test_knowledge_integration import policy
    task, settings, root, store, material = policy(tmp_path)
    settings = settings.model_copy(update={"plan_enabled": True})
    seen, original = {}, engine.run_phase
    def capture(**kwargs):
        seen.setdefault(kwargs["phase"], []).append(kwargs["payload"])
        return original(**kwargs)
    monkeypatch.setattr(engine, "run_phase", capture)
    first = workflow.start(task, settings, root,
        model=FakeModel(responses=[plan_question("空输入返回空结果还是报错？")]))
    assert first["status"] == "needs_input" and first["phase"] == "plan"
    assert len(first["question_batches"]) == 1 and first["question_batches"][0]["batch"] == 1
    assert first["model_calls"] == 1
    assert seen["plan"][0]["project_materials"]["required"][0]["id"] == material["id"]
    answered = reply(first, root, "返回空结果")
    assert answered["status"] == "paused" and answered["task_brief_revision"] == 1
    done = workflow.resume(first["run_id"], settings, root, model=FakeModel(responses=[
        plan_ready(), write(), implement_done(), review_approved()]))
    assert done["status"] == "awaiting_approval", done
    assert done["requirements_revision"] == 1
    assert done["question_batches"][0]["answered_at"]
    assert done["task_brief"]["revision"] == 2
    assert done["task_brief"]["resolved"][0]["answer"] == "返回空结果"
    assert seen["plan"][-1]["human_answers"][0]["answer"] == "返回空结果"


def test_three_batches_then_final_gap_keeps_counts_and_resumes(tmp_path):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    model = FakeModel(responses=[
        plan_question("第一批问题"), plan_question("第二批问题"), plan_question("第三批问题"),
        plan_question("第四批问题"), plan_ready(), write(), implement_done(), review_approved()])
    result = workflow.start(task, settings, root, model=model)
    for expected_batch in (1, 2, 3):
        assert result["status"] == "needs_input", result
        request = pending(result)
        assert request["batch"] == expected_batch and request["final"] is False
        human.answer(result["run_id"], root, request_id=request["id"],
                     version=request["version"], text=f"第{expected_batch}批答复")
        result = workflow.resume(result["run_id"], settings, root, model=model)
    assert result["status"] == "needs_input"
    assert result["stop_reason"] == "question_limit_reached"
    assert len(result["question_batches"]) == 3
    assert len(result["question_handovers"]) == 1
    final = pending(result)
    assert final["final"] is True and final["batch"] is None
    assert final["questions"] == ["第四批问题"]
    human.answer(result["run_id"], root, request_id=final["id"],
                 version=final["version"], text="负责人补齐范围")
    done = workflow.resume(result["run_id"], settings, root, model=model)
    assert done["status"] == "awaiting_approval", done
    state = JobStore(root).get(result["run_id"])
    assert len(state["question_batches"]) == 3
    assert state["question_handovers"][0]["answered_at"]
    assert state["requirements_revision"] == 4


@pytest.mark.parametrize("subtasks,split,reason", [
    ([subtask("a", depends_on=["b"]), subtask("b", depends_on=["a"])], "sequential", "dependency_cycle"),
    ([subtask("a", depends_on=["missing"])], "none", "unknown_dependency"),
])
def test_invalid_dependency_plan_does_not_run_implementation(tmp_path, subtasks, split, reason):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[plan_ready(subtasks, split=split)]))
    assert result["status"] == "phase_error", result
    assert result["stop_reason"] == f"invalid_plan_{reason}"
    assert result["plan"] is None
    assert result["model_calls"] == 1
    assert result["report_status"] is None


def test_multiple_subtasks_run_in_dependency_order_and_commit_each(tmp_path):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    subtasks = [subtask("first"), subtask("second", depends_on=["first"], title="第二步")]
    result = workflow.start(task, settings, root, model=FakeModel(responses=[
        plan_ready(subtasks, split="sequential"), write(), implement_done(), write(3), implement_done(),
        review_approved()]))
    assert result["status"] == "awaiting_approval", result
    results = result["plan_progress"]["results"]
    assert [item["id"] for item in results] == ["first", "second"]
    assert all(item["status"] == "completed" and item["commit"] for item in results)
    assert results[0]["commit"] != results[1]["commit"]
    assert result["plan_status"]["completed"] == 2


def test_missing_prerequisite_artifact_stops_before_another_model_call(tmp_path):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    subtasks = [subtask("first"), subtask("second", depends_on=["first"])]
    first = workflow.start(task, settings, root,
        model=FakeModel(responses=[plan_ready(subtasks, split="sequential")]), stop_after="plan")
    state = JobStore(root).get(first["run_id"])
    state["plan_progress"]["results"] = [
        {"id": "first", "title": "第一步", "status": "completed", "summary": "已完成",
         "commit": "0" * 40, "evidence": [], "decisions": [], "started_at": None, "finished_at": None}]
    JobStore(root).save(state)
    result = workflow.resume(first["run_id"], settings, root, model=FakeModel(responses=[]))
    assert result["status"] == "phase_error", result
    assert result["stop_reason"] == "invalid_plan_dependency_artifact_missing"
    assert result["model_calls"] == 1


def test_question_limit_is_shared_across_plan_implement_and_review(tmp_path):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    implement_question = turn("ImplementationReport", {"status": "needs_clarification",
        "summary": "实现需要确认", "evidence": [], "questions": ["选择哪种返回格式？"], "next_steps": []})
    review_question = turn("ReviewReport", {"verdict": "unable_to_review", "summary": "审核需要确认",
        "findings": [], "questions": ["是否保留旧字段？"]})
    result = workflow.start(task, settings, root, model=FakeModel(responses=[
        plan_ready(), write(), implement_question]))
    assert result["status"] == "needs_input"
    assert pending(result)["batch"] == 1 and pending(result)["phase"] == "implement"
    human.answer(result["run_id"], root, request_id=pending(result)["id"],
                 version=pending(result)["version"], text="返回 JSON")
    # 答复改变任务说明后先自动重规划，再重新执行计划；不会沿用旧计划的子任务进度。
    result = workflow.resume(result["run_id"], settings, root,
        model=FakeModel(responses=[plan_ready(), write(3), implement_done(), review_question]))
    assert result["status"] == "needs_input"
    request = pending(result)
    assert request["batch"] == 2 and request["phase"] == "review"
    assert [item["batch"] for item in result["question_batches"]] == [1, 2]
    assert len(result["plan_history"]) == 2
    human.answer(result["run_id"], root, request_id=request["id"],
                 version=request["version"], text="保留旧字段")
    done = workflow.resume(result["run_id"], settings, root,
        model=FakeModel(responses=[plan_ready(), write(4), implement_done(), review_approved()]))
    assert done["status"] == "awaiting_approval", done
    assert len(done["question_batches"]) == 2
    assert len(done["plan_refresh_history"]) == 2


class FakeMappingForge:
    """provider 钩子层替身；不是真实平台，也不发网络请求。"""

    def __init__(self, *, native=True, creation="supported"):
        self.native, self.creation = native, creation
        self.created, self.links, self.preflights = [], 0, 0

    def preflight(self, url, *, owner=None):
        self.preflights += 1
        return {"provider": "github", "host": "github.com", "repository": "owner/repo",
                "issue": {"number": 1, "url": url}, "viewer": "repopilot-bot",
                "registered_owner": owner,
                "comments": {"status": "supported", "evidence": "synthetic"},
                "native_sub_issues": {"status": "supported" if self.native else "unsupported",
                                      "evidence": ("native sub-issues" if self.native
                                                   else "gitlab_native_sub_issues_unavailable")},
                "issue_creation": {"status": self.creation, "evidence": "synthetic"},
                "checked_at": "2026-10-27T00:00:00+00:00"}

    def find_issue_by_marker(self, repository, marker):
        return next((item for item in self.created if marker in item["body"]), None)

    def create_issue(self, repository, *, title, body):
        item = {"id": 100 + len(self.created), "number": 2 + len(self.created),
                "url": f"https://github.com/owner/repo/issues/{2 + len(self.created)}",
                "title": title, "body": body, "state": "open"}
        self.created.append(item)
        return item

    def link_sub_issue(self, repository, parent, child_id):
        self.links += 1
        return {"native": self.native,
                "reason": "synthetic" if self.native else "gitlab_native_sub_issues_unavailable"}


def test_subtask_mapping_is_created_once_and_internal_steps_are_not_tickets(tmp_path):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    subtasks = [subtask("first", track="issue", track_reason="需要独立跟踪"),
                subtask("second", depends_on=["first"], track="none")]
    first = workflow.start(task, settings, root, issue=ISSUE,
        model=FakeModel(responses=[plan_ready(subtasks, split="sequential")]), stop_after="plan")
    forge = FakeMappingForge()
    mapped = mapping.map_subtasks(first["run_id"], root, forge)
    assert mapped["created"] == 1 and len(forge.created) == 1
    assert "<!-- repopilot-subtask:" in forge.created[0]["body"]
    assert mapped["mappings"][0]["kind"] == "native"
    assert forge.links == 1
    again = mapping.map_subtasks(first["run_id"], root, forge)
    assert again["created"] == 0 and again["reused"] == 1
    assert len(forge.created) == 1, "重复映射不能重复创建外部工单"
    state = JobStore(root).get(first["run_id"])
    assert [item["subtask"] for item in state["subtask_mappings"]] == ["first"]


def test_mapping_reuses_marker_after_lost_response(tmp_path):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    subtasks = [subtask("first", track="issue", track_reason="需要独立跟踪"), subtask("second")]
    first = workflow.start(task, settings, root, issue=ISSUE,
        model=FakeModel(responses=[plan_ready(subtasks, split="sequential")]), stop_after="plan")
    forge = FakeMappingForge()

    def lose_response(repository, *, title, body):
        item = FakeMappingForge.create_issue(forge, repository, title=title, body=body)
        raise RuntimeError("synthetic transport loss")
    forge.create_issue = lose_response
    with pytest.raises(RuntimeError):
        mapping.map_subtasks(first["run_id"], root, forge)
    mapped = mapping.map_subtasks(first["run_id"], root, forge)
    assert mapped["created"] == 0 and mapped["reused"] == 1
    assert mapped["mappings"][0]["status"] == "reused"
    assert len(forge.created) == 1


def test_gitlab_style_mapping_is_linked_not_native(tmp_path):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    subtasks = [subtask("first", track="issue", track_reason="需要独立跟踪"), subtask("second")]
    first = workflow.start(task, settings, root, issue=ISSUE,
        model=FakeModel(responses=[plan_ready(subtasks, split="sequential")]), stop_after="plan")
    mapped = mapping.map_subtasks(first["run_id"], root, FakeMappingForge(native=False))
    assert mapped["mappings"][0]["kind"] == "linked"
    assert "unavailable" in mapped["mappings"][0]["note"]
    assert mapped["native_sub_issues"] is False


def test_platform_without_issue_creation_keeps_local_plan(tmp_path):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    subtasks = [subtask("first", track="issue", track_reason="需要独立跟踪"), subtask("second")]
    first = workflow.start(task, settings, root, issue=ISSUE,
        model=FakeModel(responses=[plan_ready(subtasks, split="sequential")]), stop_after="plan")
    forge = FakeMappingForge(creation="unsupported")
    mapped = mapping.map_subtasks(first["run_id"], root, forge)
    assert mapped["mappings"][0]["status"] == "local_only"
    assert mapped["mappings"][0]["kind"] == "local"
    assert forge.created == []


class FakeCommentForge:
    def __init__(self, *, supported=True):
        self.supported = supported
        self.comments, self.posts = [], 0

    def close(self):
        return None

    def preflight(self, url, *, owner=None):
        return {"provider": "github", "host": "github.com", "repository": "owner/repo",
                "issue": {"number": 1, "url": url}, "viewer": "repopilot-bot",
                "registered_owner": owner,
                "comments": {"status": "supported" if self.supported else "unsupported"},
                "native_sub_issues": {"status": "supported"},
                "issue_creation": {"status": "supported"},
                "checked_at": "2026-10-27T00:00:00+00:00"}

    def find_issue_comment(self, repository, number, marker):
        return next((item for item in self.comments if marker in item["body"]), None)

    def add_issue_comment(self, repository, number, body):
        self.posts += 1
        value = {"id": 100 + self.posts, "url": f"https://github.com/owner/repo/issues/1#issuecomment-{100 + self.posts}",
                 "body": body, "author": "repopilot-bot", "author_type": "Bot",
                 "created_at": "2026-10-27T00:00:00Z", "system": False}
        self.comments.append(value)
        return value

    def issue_comments(self, repository, number):
        return list(self.comments)

    def add_owner_comment(self, text, author="owner-login"):
        value = {"id": 500 + len(self.comments), "url": "https://github.com/owner/repo/issues/1#issuecomment-5",
                 "body": text, "author": author, "author_type": "User",
                 "created_at": "2026-10-27T00:01:00Z", "system": False}
        self.comments.append(value)
        return value


def waiting_plan_question(tmp_path, monkeypatch):
    monkeypatch.setenv("REPOPILOT_OWNER", "owner-login")
    task, settings, root = setup(tmp_path, plan_enabled=True)
    result = workflow.start(task, settings, root, issue=ISSUE,
        model=FakeModel(responses=[plan_question("空输入返回空结果还是报错？")]))
    assert result["status"] == "needs_input"
    return result, root


def test_issue_comment_post_is_numbered_marked_and_not_repeated(tmp_path, monkeypatch):
    result, root = waiting_plan_question(tmp_path, monkeypatch)
    forge = FakeCommentForge()
    posted = human.post_issue(result["run_id"], root, forge=forge)
    assert posted["delivery"]["status"] == "delivered"
    assert forge.posts == 1
    body = forge.comments[0]["body"]
    assert "第 1 批集中澄清（1/3）" in body and "1. 空输入返回空结果还是报错？" in body
    assert "<!-- repopilot-question:" in body
    again = human.post_issue(result["run_id"], root, forge=forge)
    assert again["reused"] is True and forge.posts == 1


def test_comment_reply_requires_registered_owner_and_ignores_quote_or_bot(tmp_path, monkeypatch):
    result, root = waiting_plan_question(tmp_path, monkeypatch)
    forge = FakeCommentForge()
    human.post_issue(result["run_id"], root, forge=forge)
    forge.add_owner_comment("> 空输入返回空结果还是报错？\n\n> 这是引用")
    forge.add_owner_comment("机器人回答", author="repopilot-bot")
    forge.add_owner_comment("外部人回答", author="stranger")
    no_reply = human.sync_issue(result["run_id"], root, forge=forge)
    assert no_reply["synced"] is False and no_reply["reason"] == "no_registered_owner_reply"
    assert JobStore(root).get(result["run_id"])["requirements_revision"] == 0
    forge.add_owner_comment("返回空结果，保留旧接口。")
    synced = human.sync_issue(result["run_id"], root, forge=forge)
    assert synced["synced"] is True and synced["status"] == "paused"
    state = JobStore(root).get(result["run_id"])
    assert state["human_answers"][0]["actor"] == "registered-owner"
    assert state["human_answers"][0]["channel"] == "issue"
    assert state["human_answers"][0]["answer"] == "返回空结果，保留旧接口。"
    assert state["task_brief"]["resolved"][0]["channel"] == "issue"
    assert state["question_batches"][0]["answered_at"]
    answered_request = next(item for item in state["human_requests"] if item["status"] == "answered")
    assert answered_request["delivery"]["status"] == "answered"
    repeat = human.sync_issue(result["run_id"], root, forge=forge)
    assert repeat["already_answered"] is True and repeat["requirements_revision"] == 1


def test_deleted_batch_comment_is_not_replaced_by_a_copied_marker(tmp_path, monkeypatch):
    from repopilot.collaboration.requests import HumanRequest
    result, root = waiting_plan_question(tmp_path, monkeypatch)
    forge = FakeCommentForge()
    human.post_issue(result["run_id"], root, forge=forge)
    request = HumanRequest.model_validate(
        next(item for item in JobStore(root).get(result["run_id"])["human_requests"]
             if item["status"] == "pending"))
    marker_text = comments.marker(request)
    forge.comments.pop(0)  # 模拟真正的批次评论被删除
    forge.comments.append({"id": 999, "url": "https://github.com/owner/repo/issues/1#issuecomment-999",
                           "body": f"复制标记 {marker_text}", "author": "stranger", "author_type": "User",
                           "created_at": "2026-10-27T00:00:30Z", "system": False})
    forge.add_owner_comment("批次评论已删除，这句话不能被当成答复")
    synced = human.sync_issue(result["run_id"], root, forge=forge)
    assert synced["synced"] is False
    assert JobStore(root).get(result["run_id"])["requirements_revision"] == 0


def test_comment_channel_refuses_without_owner_or_capability(tmp_path, monkeypatch):
    result, root = waiting_plan_question(tmp_path, monkeypatch)
    monkeypatch.delenv("REPOPILOT_OWNER", raising=False)
    monkeypatch.setattr(comments, "default_env_file", lambda: tmp_path / "missing.env")
    with pytest.raises(StateError, match="registered_owner_required"):
        human.post_issue(result["run_id"], root, forge=FakeCommentForge())
    monkeypatch.setenv("REPOPILOT_OWNER", "owner-login")
    with pytest.raises(StateError, match="issue_comments_unavailable"):
        human.post_issue(result["run_id"], root, forge=FakeCommentForge(supported=False))
    state = JobStore(root).get(result["run_id"])
    assert state["human_requests"][0]["delivery"] is None


def test_comment_reply_resume_exports_material_with_comment_source(tmp_path, monkeypatch):
    from tests.runtime.test_knowledge_integration import policy
    monkeypatch.setenv("REPOPILOT_OWNER", "owner-login")
    task, settings, root, store, material = policy(tmp_path)
    settings = settings.model_copy(update={"plan_enabled": True})
    first = workflow.start(task, settings, root, issue=ISSUE,
        model=FakeModel(responses=[plan_question("空输入返回空结果还是报错？")]))
    forge = FakeCommentForge()
    human.post_issue(first["run_id"], root, forge=forge)
    forge.add_owner_comment("返回空结果，保留旧接口。")
    synced = human.sync_issue(first["run_id"], root, forge=forge)
    assert synced["synced"] is True
    done = workflow.resume(first["run_id"], settings, root, model=FakeModel(responses=[
        plan_ready(), write(), implement_done(), review_approved()]))
    assert done["status"] == "awaiting_approval", done
    state = JobStore(root).get(first["run_id"])
    identifier = next(iter(state["knowledge_answers"].values()))
    doc = store.inspect(task.repository, identifier)
    assert doc["status"] == "confirmed"
    assert doc["sources"][-1].startswith("https://github.com/")
    assert "返回空结果" in store.read(task.repository, identifier, version=doc["version"])["text"]


def test_local_task_cannot_post_or_map_to_a_platform(tmp_path, monkeypatch):
    monkeypatch.setenv("REPOPILOT_OWNER", "owner-login")
    task, settings, root = setup(tmp_path, plan_enabled=True)
    result = workflow.start(task, settings, root,
        model=FakeModel(responses=[plan_question("本地问题")]))
    with pytest.raises(StateError, match="issue_required"):
        human.post_issue(result["run_id"], root, forge=FakeCommentForge())
    first = workflow.start(task, settings, root,
        model=FakeModel(responses=[plan_ready([
            subtask("first", track="issue", track_reason="需要独立跟踪"),
            subtask("second", depends_on=["first"])], split="sequential")]), stop_after="plan")
    with pytest.raises(StateError, match="issue_required_for_subtask_mapping"):
        mapping.map_subtasks(first["run_id"], root, FakeMappingForge())


def test_retry_reuses_recorded_plan_without_calling_planner_again(tmp_path):
    task, settings, root = setup(tmp_path, plan_enabled=True, max_model_calls=1)
    first = workflow.start(task, settings, root,
        model=FakeModel(responses=[plan_ready()]))
    assert first["status"] == "limit_reached", first
    plan_version = JobStore(root).get(first["run_id"])["plan"]["version"]
    second = workflow.retry(first["run_id"], settings, root, reason="给足实现额度",
        overrides={"max_model_calls": 6},
        model=FakeModel(responses=[write(), implement_done(), review_approved()]))
    assert second["status"] == "awaiting_approval", second
    state = JobStore(root).get(second["run_id"])
    assert state["plan"]["version"] == plan_version
    assert state["plan_progress"]["reused"] is True
    assert planner.status(state)["completed"] == 1
    assert second["model_calls"] == 3


def test_report_shows_brief_plan_modules_and_question_batches(tmp_path):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[
        plan_ready(), write(), implement_done(), review_approved()]))
    state = JobStore(root).get(result["run_id"])
    html = build(state, root / "runs" / result["run_id"], Path.cwd())
    assert "任务说明与计划" in html and "子任务" in html
    assert result["plan"]["version"] in html
    assert "执行顺序" not in html  # 程序只展示事实，不编造未记录的计划理由


def test_plan_and_human_list_commands_need_no_model_credentials(tmp_path, monkeypatch):
    import repopilot.cli as cli
    task, settings, root = setup(tmp_path, plan_enabled=True)
    result = workflow.start(task, settings, root,
        model=FakeModel(responses=[plan_ready()]), stop_after="plan")
    def unavailable(*_, **__):
        pytest.fail("本地计划与请求查看不应加载模型凭证")
    monkeypatch.setattr(cli, "load_settings", unavailable)
    args = cli.build_parser().parse_args(["plan", "show", result["run_id"], "--output", str(root)])
    shown = cli.execute_command(args)
    assert shown["plan"]["version"] == result["plan"]["version"]
    args = cli.build_parser().parse_args(["human", "list", result["run_id"], "--output", str(root)])
    listed = cli.execute_command(args)
    assert listed["status"] == "ok" and listed["task_brief"]["revision"] == 1


def note(identifier, body, author, created_at, *, author_type="User", system=False):
    return {"id": identifier, "body": body, "author": author, "author_type": author_type,
            "created_at": created_at, "system": system,
            "url": f"https://github.com/owner/repo/issues/1#issuecomment-{identifier}"}


def request_for(question="空输入返回什么？", identifier="human-f1-test"):
    return HumanRequest(id=identifier, kind="clarification", phase="plan", summary="需要确认",
                        questions=[question], context={}, version="v",
                        created_at="2026-10-27T00:00:00Z")


def test_descending_notes_still_take_the_reply_after_the_batch():
    """GitLab Notes默认desc：不能把列表位置当成时间顺序。"""
    request = request_for()
    marker = comments.marker(request)
    values = [
        note(3, "返回空结果", "owner-login", "2026-10-27T00:03:00Z"),
        note(2, f"{marker}\n## RepoPilot 第 1 批集中澄清", "repopilot-bot",
             "2026-10-27T00:02:00Z", author_type="Bot"),
        note(1, "旧讨论里的一句话：不要动这个接口。", "owner-login", "2026-10-27T00:01:00Z"),
    ]
    accepted, later = comments.select_reply(values, request, owner="owner-login",
                                            batch_id=2, batch_created_at="2026-10-27T00:02:00Z")
    assert accepted["comment_id"] == 3 and accepted["text"] == "返回空结果"
    assert not any(item.get("comment_id") == 1 for item in later)


def test_quoted_batch_reply_is_accepted_after_removing_the_quote():
    request = request_for()
    marker = comments.marker(request)
    values = [
        note(2, f"{marker}\n## RepoPilot 第 1 批集中澄清", "repopilot-bot",
             "2026-10-27T00:02:00Z", author_type="Bot"),
        note(4, f"> {marker}\n> 空输入返回什么？\n\n返回空列表。", "owner-login",
             "2026-10-27T00:04:00Z"),
        note(5, f"> {marker}\n> 空输入返回什么？", "owner-login", "2026-10-27T00:05:00Z"),
    ]
    accepted, later = comments.select_reply(values, request, owner="owner-login",
                                            batch_id=2, batch_created_at="2026-10-27T00:02:00Z")
    assert accepted["text"] == "返回空列表。"
    assert any(item.get("ignored_reason") == "quoted_or_echo" for item in later)


def test_planning_is_enabled_by_default_and_cli_can_turn_it_off(tmp_path):
    assert RuntimeSettings(api_key="synthetic").plan_enabled is True
    args = cli.build_parser().parse_args(["run", "task.yaml", "--no-plan"])
    assert args.plan_enabled is False
    task, _, root = setup(tmp_path)
    settings = RuntimeSettings(api_key="synthetic-private-key", issue_comments_enabled=False)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[
        plan_ready(), write(), implement_done(), review_approved()]))
    assert result["status"] == "awaiting_approval"
    assert result["plan"]["split"] == "none" and result["model_calls"] == 4


def test_status_command_and_report_show_plan_progress(tmp_path):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[
        plan_ready(), write(), implement_done(), review_approved()]))
    args = cli.build_parser().parse_args(["status", result["run_id"], "--output", str(root)])
    values = cli.execute_command(args)
    assert values["plan_status"]["completed"] == 1 and values["plan_status"]["total"] == 1
    html = build(JobStore(root).get(result["run_id"]), root / "runs" / result["run_id"], Path.cwd())
    assert "完成 1/1" in html


def test_answer_after_plan_replans_and_exposes_brief_versions(tmp_path, monkeypatch):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    implement_question = turn("ImplementationReport", {"status": "needs_clarification",
        "summary": "实现需要确认", "evidence": [], "questions": ["使用哪个字段？"], "next_steps": []})
    payloads = {}
    original = engine.run_phase
    def capture(**kwargs):
        payloads.setdefault(kwargs["phase"], []).append(kwargs["payload"])
        return original(**kwargs)
    monkeypatch.setattr(engine, "run_phase", capture)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[
        plan_ready(), write(), implement_question]))
    assert result["status"] == "needs_input"
    old_plan = result["plan"]["version"]
    answered = human.answer(result["run_id"], root, request_id=pending(result)["id"],
                            version=pending(result)["version"], text="使用 old_field")
    assert answered["plan_refresh_required"] is True
    state = JobStore(root).get(result["run_id"])
    assert state["phase"] == "plan" and state["plan_refresh_history"]
    html = build(state, root / "runs" / result["run_id"], Path.cwd())
    assert "计划基于旧版任务说明" in html
    done = workflow.resume(result["run_id"], settings, root, model=FakeModel(responses=[
        plan_ready(), write(3), implement_done(), review_approved()]))
    assert done["status"] == "awaiting_approval", done
    assert done["plan"]["version"] != old_plan
    assert len(done["plan_refresh_history"]) == 1
    assert payloads["implement"][-1]["plan"]["brief_revision"] == done["task_brief"]["revision"]
    assert payloads["implement"][-1]["plan"]["stale"] is False
    assert payloads["review"][-1]["plan"]["current_brief_revision"] == done["task_brief"]["revision"]
    shown = mapping.show(result["run_id"], root)
    assert len(shown["task_brief_versions"]) >= 2 and shown["plan_refresh_history"]
    assert shown["task_brief_history"]


def test_after_batch_limit_each_new_gap_keeps_its_own_final_record(tmp_path):
    task, settings, root = setup(tmp_path, plan_enabled=True, max_question_batches=1)
    model = FakeModel(responses=[plan_question("第一批问题"), plan_question("最终缺口一"),
                                 plan_question("最终缺口二")])
    result = workflow.start(task, settings, root, model=model)
    assert result["status"] == "needs_input" and pending(result)["batch"] == 1
    human.answer(result["run_id"], root, request_id=pending(result)["id"],
                 version=pending(result)["version"], text="补充一")
    result = workflow.resume(result["run_id"], settings, root, model=model)
    assert result["status"] == "needs_input" and pending(result)["final"] is True
    human.answer(result["run_id"], root, request_id=pending(result)["id"],
                 version=pending(result)["version"], text="补充二")
    result = workflow.resume(result["run_id"], settings, root, model=model)
    assert result["status"] == "needs_input" and pending(result)["final"] is True
    assert len(result["question_batches"]) == 1
    assert len(result["question_handovers"]) == 2


def test_blocked_subtask_is_visible_and_can_be_retried(tmp_path):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    blocked = turn("ImplementationReport", {"status": "blocked", "summary": "缺少运行环境",
        "evidence": [], "next_steps": [], "questions": []})
    first = workflow.start(task, settings, root, model=FakeModel(responses=[plan_ready(), blocked]))
    assert first["status"] == "completed", first
    assert first["stop_reason"] == "subtask_blocked"
    assert first["plan_status"]["state"] == "blocked"
    assert "计划子任务报告阻塞" in next_action(JobStore(root).get(first["run_id"]))
    plan_version = first["plan"]["version"]
    second = workflow.retry(first["run_id"], settings, root, reason="补齐环境后继续",
        model=FakeModel(responses=[write(), implement_done(), review_approved()]))
    assert second["status"] == "awaiting_approval", second
    state = JobStore(root).get(second["run_id"])
    assert state["plan"]["version"] == plan_version
    assert planner.status(state)["completed"] == 1


def test_sync_after_local_answer_reports_already_answered(tmp_path, monkeypatch):
    result, root = waiting_plan_question(tmp_path, monkeypatch)
    forge = FakeCommentForge()
    human.post_issue(result["run_id"], root, forge=forge)
    request = pending(result)
    human.answer(result["run_id"], root, request_id=request["id"],
                 version=request["version"], text="本地先答复")
    synced = human.sync_issue(result["run_id"], root, forge=forge)
    assert synced["already_answered"] is True
    assert synced["answered_channel"] == "cli"
    assert synced["requirements_revision"] == 1
    assert JobStore(root).get(result["run_id"])["requirements_revision"] == 1


def test_subtask_issue_body_with_secret_is_rejected_before_creation(tmp_path):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    subtasks = [subtask("first", goal="使用 sk-" + "a" * 30, track="issue",
                        track_reason="需要独立跟踪"), subtask("second")]
    first = workflow.start(task, settings, root, issue=ISSUE,
        model=FakeModel(responses=[plan_ready(subtasks, split="sequential")]), stop_after="plan")
    forge = FakeMappingForge()
    with pytest.raises(StateError):
        mapping.map_subtasks(first["run_id"], root, forge)
    assert forge.created == []
    state = JobStore(root).get(first["run_id"])
    assert state["subtask_mappings"][0]["status"] == "failed"


def test_multi_subtask_report_distinguishes_each_subtask(tmp_path):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    subtasks = [subtask("first"), subtask("second", depends_on=["first"])]
    result = workflow.start(task, settings, root, model=FakeModel(responses=[
        plan_ready(subtasks, split="sequential"), write(), implement_done(), write(3),
        implement_done(), review_approved()]))
    assert result["status"] == "awaiting_approval"
    html = build(JobStore(root).get(result["run_id"]), root / "runs" / result["run_id"], Path.cwd())
    assert "子任务实现报告" in html and "只属于最后一个子任务" in html
    assert "完成 2/2" in html


def test_issue_waiting_run_auto_posts_and_watch_scans_it(tmp_path, monkeypatch):
    monkeypatch.setenv("REPOPILOT_OWNER", "owner-login")
    task, settings, root = setup(tmp_path, plan_enabled=True, issue_comments_enabled=True)
    forge = FakeCommentForge()
    monkeypatch.setattr("repopilot.delivery.forge.open_forge", lambda *_, **__: forge)
    result = workflow.start(task, settings, root, issue=ISSUE,
        model=FakeModel(responses=[plan_question("自动投递的问题")]))
    assert result["status"] == "needs_input"
    assert result["auto_comment"]["status"] == "posted"
    assert forge.posts == 1
    state = JobStore(root).get(result["run_id"])
    assert state["human_requests"][0]["delivery"]["status"] == "delivered"
    assert result["run_id"] in human.waiting_comment_runs(root)

    calls = []
    monkeypatch.setattr(watcher, "load_settings", lambda: settings)
    monkeypatch.setattr(watcher.workflow, "resume",
                        lambda *_, **__: calls.append("resume") or {"status": "needs_input"})
    monkeypatch.setattr(watcher.human, "sync_issue",
                        lambda *_, **__: calls.append("sync") or {"synced": True})
    handled = watcher.run_waiting_job(root, result["run_id"])
    assert calls == ["resume", "sync", "resume"]
    assert handled["kind"] == "comment"


def test_auto_comment_failure_keeps_the_run_waiting(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOPILOT_OWNER", raising=False)
    monkeypatch.setattr(comments, "default_env_file", lambda: tmp_path / "missing.env")
    monkeypatch.setattr("repopilot.delivery.forge.open_forge", lambda *_, **__: FakeCommentForge())
    task, settings, root = setup(tmp_path, plan_enabled=True, issue_comments_enabled=True)
    result = workflow.start(task, settings, root, issue=ISSUE,
        model=FakeModel(responses=[plan_question("没有负责人时的问题")]))
    assert result["status"] == "needs_input"
    assert result["auto_comment"]["status"] == "failed"
    assert result["auto_comment"]["reason"] == "registered_owner_required"
    assert JobStore(root).get(result["run_id"])["human_requests"][0]["delivery"] is None
    assert watcher.WatchConfig(targets=[watcher.WatchTarget(task=tmp_path / "task.yaml")]).poll_comments is True


def test_legacy_settings_do_not_gain_new_planning_or_comment_defaults():
    old = RuntimeSettings(api_key="synthetic-old", plan_enabled=False,
                          issue_comments_enabled=False).model_dump(exclude={"api_key"})
    old.pop("plan_enabled")
    old.pop("issue_comments_enabled")
    current = RuntimeSettings(api_key="synthetic-new")
    frozen = workflow.frozen_settings(old, current)
    assert frozen.plan_enabled is False and frozen.issue_comments_enabled is False
    explicit = workflow.frozen_settings(old, current, {"plan_enabled": True})
    assert explicit.plan_enabled is True


def test_legacy_run_resume_and_retry_do_not_gain_a_plan_phase(tmp_path):
    task, settings, root = setup(tmp_path)
    state = workflow.create_job(task, settings, root)
    saved = JobStore(root).get(state["run_id"])
    saved["settings"].pop("plan_enabled")
    saved["settings"].pop("issue_comments_enabled")
    JobStore(root).save(saved)
    resumed = workflow.resume(state["run_id"], settings, root,
        model=FakeModel(responses=[write(), implement_done(), review_approved()]))
    assert resumed["status"] == "awaiting_approval"
    assert resumed["plan"] is None and resumed["model_calls"] == 3

    limited_settings = settings.model_copy(update={"max_model_calls": 1})
    limited = workflow.start(task, limited_settings, root,
        model=FakeModel(responses=[write(), implement_done()]))
    assert limited["status"] == "limit_reached"
    old = JobStore(root).get(limited["run_id"])
    old["settings"].pop("plan_enabled")
    old["settings"].pop("issue_comments_enabled")
    JobStore(root).save(old)
    retried = workflow.retry(limited["run_id"], limited_settings, root, reason="旧运行继续",
        overrides={"max_model_calls": 5},
        model=FakeModel(responses=[write(3), implement_done(), review_approved()]))
    assert retried["status"] == "awaiting_approval"
    assert retried["plan"] is None and retried["model_calls"] == 3


def test_replan_passes_previous_plan_and_progress_to_planner_and_implementer(tmp_path, monkeypatch):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    subtasks = [subtask("first"), subtask("second", depends_on=["first"])]
    implement_question = turn("ImplementationReport", {"status": "needs_clarification",
        "summary": "第二步需要确认", "evidence": [], "questions": ["第二步如何做？"], "next_steps": []})
    payloads = {}
    original = engine.run_phase
    def capture(**kwargs):
        payloads.setdefault(kwargs["phase"], []).append(kwargs["payload"])
        return original(**kwargs)
    monkeypatch.setattr(engine, "run_phase", capture)
    first = workflow.start(task, settings, root, model=FakeModel(responses=[
        plan_ready(subtasks, split="sequential"), write(), implement_done(), implement_question]))
    assert first["status"] == "needs_input"
    old_progress = JobStore(root).get(first["run_id"])["plan_progress"]
    assert old_progress["results"][0]["status"] == "completed"
    human.answer(first["run_id"], root, request_id=pending(first)["id"],
                 version=pending(first)["version"], text="按旧字段实现")
    done = workflow.resume(first["run_id"], settings, root, model=FakeModel(responses=[
        plan_ready(subtasks, split="sequential"), write(3), implement_done(),
        write(4), implement_done(), review_approved()]))
    assert done["status"] == "awaiting_approval", done
    assert payloads["plan"][-1]["previous_plan"]["old_progress"]["results"][0]["status"] == "completed"
    assert payloads["plan"][-1]["previous_plan"]["old_progress"]["results"][0]["commit"] == old_progress["results"][0]["commit"]
    refresh = payloads["implement"][-1]["plan_refresh"]
    assert refresh["old_plan"] and refresh["old_progress"]["results"][0]["commit"]
    assert "先核对旧实现" in refresh["note"]
    latest = done["plan_refresh_history"][-1]
    assert latest["new_plan"] == done["plan"]["version"]
    html = build(JobStore(root).get(first["run_id"]), root / "runs" / first["run_id"], Path.cwd())
    assert "重规划记录" in html
