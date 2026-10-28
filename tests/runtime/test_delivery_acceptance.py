"""M6组合链路：真实本地Git/SQLite/检查，确定性模型和平台替身，无网络写入。"""

import pytest

from repopilot.core.config import Check
from repopilot.delivery.forge import publish
from repopilot.delivery.github import GitHubError
from repopilot.execution.workspace import head_commit, run_git
from repopilot.orchestration import human, workflow
from repopilot.presentation.report import build
from repopilot.storage.jobs import JobStore, StateError
from tests.runtime.test_github import FakeGitHub, ISSUE
from tests.runtime.test_planning import (FakeCommentForge, plan_question, plan_ready, subtask, write)
from tests.runtime.test_review_loop import FakeModel, implement_done, review_approved, review_changes


def test_comment_clarification_to_review_rework_and_reconciled_draft(tmp_path, monkeypatch):
    from tests.runtime.test_knowledge_integration import policy

    monkeypatch.setenv("REPOPILOT_OWNER", "owner-login")
    task, settings, root, _, _ = policy(tmp_path)
    settings = settings.model_copy(update={"plan_enabled": True})
    # 真正执行候选代码，拒绝只声称通过的实现报告。
    task = task.model_copy(update={"checks": {"unit": Check(argv=["{python}", "-c",
        "from app import value; assert value >= 2, value"])}})
    run_git(task.repository, "remote", "add", "origin", "https://github.com/owner/repo.git")
    baseline = head_commit(task.repository)
    first = workflow.start(task, settings, root, issue=ISSUE,
        model=FakeModel(responses=[plan_question("取值应增加到多少？")]))
    run_id = first["run_id"]
    assert first["status"] == "needs_input"
    waiting = JobStore(root).get(run_id)
    before_control = waiting["control"]
    comments = FakeCommentForge()
    human.post_issue(run_id, root, forge=comments)
    human.post_issue(run_id, root, forge=comments)
    assert comments.posts == 1
    assert workflow.resume(run_id, settings, root)["status"] == "needs_input"
    assert JobStore(root).get(run_id)["control"] == before_control
    comments.add_owner_comment("先增加到2，再增加到3。")
    assert human.sync_issue(run_id, root, forge=comments)["synced"] is True
    assert JobStore(root).get(run_id)["approval"] is None

    # 重新打开持久化现场，两个依赖子任务后，对组合候选进行检查与双审、再返工。
    tasks = [subtask("first"), subtask("second", depends_on=["first"])]
    candidate = workflow.resume(run_id, settings, root, model=FakeModel(responses=[
        plan_ready(tasks, split="sequential"), write(2), implement_done(), write(3), implement_done(),
        *review_changes(), write(4), implement_done(), *review_approved()]))
    assert candidate["status"] == "awaiting_approval", candidate
    store = JobStore(root)
    state = store.get(run_id)
    assert [item["id"] for item in state["plan_progress"]["results"]] == ["first", "second"]
    assert len(state["question_batches"]) == 1 and state["reworks"] == 1
    assert len(state["history"]) == 2
    assert set(state["review_report"]["perspectives"]) == {"correctness", "context"}
    assert state["verified"][0]["exit_code"] == 0
    assert state["knowledge_answers"] and state["knowledge_snapshot"]["entries"]
    api = FakeGitHub(state)
    with pytest.raises(StateError, match="matching_human_approval_required"):
        publish(run_id, root, api)
    assert api.posts == api.pushes == 0
    waiting_html = build(state, root / "runs" / run_id, root)
    for value in ("当前等待事项", "approval", "owner-login", "registered-owner", "issuecomment-5", "compatibility"):
        assert value in waiting_html
    workflow.decide(run_id, root, commit=state["reviewed_commit"], approved=True, reason="确定性验收批准")
    approved_control = store.get(run_id)["control"]
    api.lose_response = True
    with pytest.raises(GitHubError, match="github_transport_error"):
        publish(run_id, root, api)
    assert store.get(run_id)["status"] == "publication_failed"
    assert api.posts == 1
    delivered = publish(run_id, root, api)
    assert delivered["status"] == "published"
    assert publish(run_id, root, api) == delivered
    final = JobStore(root).get(run_id)
    assert api.posts == api.pushes == 1
    assert final["control"] == approved_control
    assert final["approval"]["commit"] == final["pull_request"]["commit"] == api.remote_head
    assert head_commit(task.repository) == baseline
    assert (task.repository / "app.py").read_text() == "value = 1\n"
    html = build(final, root / "runs" / run_id, root)
    for value in (ISSUE["url"], "first", "second", "版本与来源", "审核与返工", "人工决定",
                  final["review_basis"]["hash"], final["approval"]["commit"], final["pull_request"]["url"]):
        assert value in html
    assert "synthetic-private-key" not in html


def test_real_check_failure_cannot_be_overridden_by_model_success(tmp_path):
    from tests.runtime.test_review_loop import setup

    task, settings, root = setup(tmp_path, plan_enabled=True)
    task = task.model_copy(update={"checks": {"unit": Check(argv=["{python}", "-c",
        "from app import value; assert value == 99, value"])}})
    run_git(task.repository, "remote", "add", "origin", "https://github.com/owner/repo.git")
    candidate = workflow.start(task, settings, root, issue=ISSUE, model=FakeModel(responses=[
        plan_ready(), write(2), implement_done(), *review_approved()]))
    state = JobStore(root).get(candidate["run_id"])
    assert state["verified"][0]["exit_code"] != 0
    with pytest.raises(StateError):
        workflow.decide(state["run_id"], root, commit=state["reviewed_commit"], approved=True, reason="不得放行失败检查")
    api = FakeGitHub(state)
    with pytest.raises(StateError, match="matching_human_approval_required"):
        publish(state["run_id"], root, api)
    assert api.posts == api.pushes == 0
    html = build(state, root / "runs" / state["run_id"], root)
    assert "未通过" in html and "AssertionError" in html
