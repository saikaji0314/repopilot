"""M3计划、集中提问与子Issue映射的本地机制验证；模型与平台均为确定性替身。"""

import json
from pathlib import Path

import pytest

from repopilot.collaboration import comments
from repopilot.agents import loop as engine
from repopilot.orchestration import human, workflow
from repopilot.planning import mapping, planner
from repopilot.planning.contracts import PlanFault
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
    result = workflow.resume(result["run_id"], settings, root,
        model=FakeModel(responses=[implement_done(), review_question]))
    assert result["status"] == "needs_input"
    request = pending(result)
    assert request["batch"] == 2 and request["phase"] == "review"
    assert [item["batch"] for item in result["question_batches"]] == [1, 2]
    human.answer(result["run_id"], root, request_id=request["id"],
                 version=request["version"], text="保留旧字段")
    done = workflow.resume(result["run_id"], settings, root,
        model=FakeModel(responses=[review_approved()]))
    assert done["status"] == "awaiting_approval", done
    assert len(done["question_batches"]) == 2


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
