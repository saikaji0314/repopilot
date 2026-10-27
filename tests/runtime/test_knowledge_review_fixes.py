"""独立审核发现的资料检索、人工决定历史和发布中断组合回归。"""

from contextlib import closing
import json

import pytest

from repopilot.delivery.forge import ForgeError, publish
from repopilot.execution.workspace import head_commit, run_git
from repopilot.knowledge.retrieval import search
from repopilot.orchestration import knowledge, workflow
from repopilot.orchestration.state import attach
from repopilot.storage.jobs import JobStore, StateError
from tests.runtime.test_github import FakeGitHub, ISSUE, approve
from repopilot.delivery.github import GitHubError
from tests.runtime.test_gitlab import FakeGitLab, ISSUE as GITLAB_ISSUE
from tests.runtime.test_knowledge import register
from tests.runtime.test_knowledge_integration import policy
from tests.runtime.test_review_loop import FakeModel, implement_done, make_repo, review_approved
from tests.runtime.test_human import write
from repopilot.knowledge.store import KnowledgeStore


def test_short_dense_optional_material_search_and_runtime(tmp_path):
    body = "# Dense\n" + "value\n" * 300
    task, settings, root, store, _ = policy(tmp_path, required=False, body=body)
    result = search(store, task.repository, "value")
    assert result["entries"]
    assert all(r["end_line"] - r["start_line"] < 200 for r in result["entries"])
    assert max(r["end_line"] for r in result["entries"]) == 301
    task = task.model_copy(update={"request": "Change value to 2"})
    done = workflow.start(task, settings, root,
        model=FakeModel(responses=[write(), implement_done(), review_approved()]))
    assert done["status"] == "awaiting_approval", done


def test_confirmation_history_survives_update_reconfirmation_and_supersession(tmp_path):
    repository, store = make_repo(tmp_path), KnowledgeStore(tmp_path / "runtime")
    original = register((repository, store))
    store.confirm(repository, "rules", version=original["version"], reason="second operator decision")
    (repository / "docs/rules.md").write_text("# Updated\n\nnew rule.\n", encoding="utf-8")
    store.sync(repository)
    current = store.inspect(repository, "rules")
    assert current["confirmation"] is None
    register((repository, store), "next")
    store.supersede(repository, "rules", replacement="next", reason="use successor")
    events = store.history(repository, "rules", version=original["version"])
    assert [e["event"] for e in events] == ["confirmed", "confirmed", "invalidated"]
    assert events[0]["reason"] == "fixture operator"
    assert events[1]["reason"] == "second operator decision"
    assert all(e["actor"] and e["source"] and e["at"] for e in events)
    old = store.read(repository, "rules", version=original["version"])
    assert old["events"] == events
    assert store.history(repository, "rules")[-1]["replacement"] == "next"


class InterruptedGitHub(FakeGitHub):
    def create_pull_request(self, *args, **kwargs):
        super().create_pull_request(*args, **kwargs)
        raise KeyboardInterrupt("remote created, local checkpoint not saved")

    def branch_head(self, path, repository, branch):
        return self.state["reviewed_commit"] if self.pushes else None


class InterruptedGitLab(FakeGitLab):
    def create_pull_request(self, *args, **kwargs):
        super().create_pull_request(*args, **kwargs)
        raise KeyboardInterrupt("remote created, local checkpoint not saved")

    def branch_head(self, path, repository, branch):
        return self.state["reviewed_commit"] if self.pushes else None


def interrupted_candidate(tmp_path, *, create=True, provider="github"):
    task, settings, root, store, material = policy(tmp_path)
    issue = ISSUE if provider == "github" else GITLAB_ISSUE
    remote = "https://github.com/owner/repo.git" if provider == "github" else "https://gitlab.com/group/project.git"
    run_git(task.repository, "remote", "add", "origin", remote)
    done = workflow.start(task, settings, root, issue=issue,
        model=FakeModel(responses=[write(), implement_done(), review_approved()]))
    state = JobStore(root).get(done["run_id"])
    approve(state, root)
    api = (InterruptedGitHub if provider == "github" else InterruptedGitLab)(state)
    if not create:
        def interrupted_push(*_):
            api.pushes += 1
            raise KeyboardInterrupt("after push, before PR")
        api.push = interrupted_push
    with pytest.raises(KeyboardInterrupt):
        publish(state["run_id"], root, api)
    assert JobStore(root).get(state["run_id"])["in_flight"] == "publish"
    (task.repository / "docs/policy.md").write_text("# Updated\n\n保留旧接口并兼容空输入。\n", encoding="utf-8")
    return task, settings, root, store, state, api


@pytest.mark.parametrize("create", [True, False])
@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_interrupted_publish_reconciles_before_context_refresh_without_new_writes(tmp_path, create, provider):
    task, settings, root, store, initial, api = interrupted_candidate(tmp_path, create=create, provider=provider)
    result = publish(initial["run_id"], root, api)
    assert result["status"] == "needs_input"
    paused = JobStore(root).get(initial["run_id"])
    assert paused["in_flight"] is None and paused["approval"] is None
    publication = paused["publication"]
    assert publication["approval"]["commit"] == initial["reviewed_commit"]
    observed = publication["reconciliations"][-1]
    assert observed["branch_head"] == initial["reviewed_commit"]
    assert bool(observed["pull_request"]) == create
    assert api.posts == int(create) and api.pushes == 1
    # 即使重复发布也不能借旧批准写入。
    with pytest.raises(StateError, match="matching_human_approval_required"):
        publish(initial["run_id"], root, api)
    request = next(r for r in paused["human_requests"] if r["status"] == "pending")
    record = store.inspect(task.repository, "compatibility")
    store.confirm(task.repository, record["id"], version=record["version"], reason="accept updated rule")
    knowledge.refresh(initial["run_id"], root, request_id=request["id"], version=request["version"],
        commit=head_commit(attach(paused, root).path), reason="recheck using current rule")
    done = workflow.resume(initial["run_id"], settings, root,
        model=FakeModel(responses=[implement_done(), review_approved()]))
    assert done["status"] == "awaiting_approval", done
    assert done["model_calls"] == initial["control"]["model_calls"] + 2
    with pytest.raises(StateError, match="matching_human_approval_required"):
        publish(initial["run_id"], root, api)
    if create:
        renewed = JobStore(root).get(initial["run_id"])
        approve(renewed, root)
        result = publish(initial["run_id"], root, api)
        assert result["status"] == "published"
        assert api.posts == 1 and api.pushes == 1
    else:
        # 远端只有分支，没有PR；重新批准后才允许创建，仍复用原发布身份。
        api.push = lambda *_: None  # 已观察到相同提交，模拟provider的幂等分支检查。
        fake = FakeGitHub if provider == "github" else FakeGitLab
        api.create_pull_request = fake.create_pull_request.__get__(api)
        approve(JobStore(root).get(initial["run_id"]), root)
        result = publish(initial["run_id"], root, api)
        assert result["status"] == "published" and api.posts == 1 and api.pushes == 1


def test_reconciliation_failure_keeps_interrupted_publication_pending(tmp_path):
    _, _, root, _, initial, api = interrupted_candidate(tmp_path)
    original = api.find_pull_request
    def unavailable(*_):
        raise GitHubError("github_http_503")
    api.find_pull_request = unavailable
    with pytest.raises(GitHubError, match="503"):
        publish(initial["run_id"], root, api)
    state = JobStore(root).get(initial["run_id"])
    assert state["in_flight"] == "publish" and state["approval"]["approved"]
    assert not any(r["kind"] == "context_update" for r in state["human_requests"])
    api.find_pull_request = original
    assert publish(initial["run_id"], root, api)["status"] == "needs_input"
    assert api.posts == 1 and api.pushes == 1


def test_legacy_dense_index_is_paginated_without_rebuilding(tmp_path):
    repository, store = make_repo(tmp_path), KnowledgeStore(tmp_path / "runtime")
    register((repository, store), body="# Dense\n" + "value\n" * 300)
    # 模拟旧索引中一个跨300行的章节，正文版本未改变。
    with closing(store.connect()) as db, db:
        rows = db.execute("SELECT rowid FROM material_chunks ORDER BY rowid").fetchall()
        for row in rows[1:]:
            db.execute("DELETE FROM material_chunks WHERE rowid=?", (row[0],))
            db.execute("DELETE FROM material_search WHERE rowid=?", (row[0],))
        db.execute("UPDATE material_chunks SET start_line=1,end_line=301")
        db.execute("UPDATE material_search SET terms='value'")
    result = search(store, repository, "value")
    assert [(r["start_line"], r["end_line"]) for r in result["entries"]] == [(1, 200), (201, 301)]
    with closing(store.connect()) as db:
        assert db.execute("SELECT end_line FROM material_chunks").fetchone()[0] == 301


def test_schema_one_migration_retains_surviving_confirmation_once(tmp_path):
    repository, store = make_repo(tmp_path), KnowledgeStore(tmp_path / "runtime")
    first = register((repository, store))
    with closing(store.connect()) as db, db:
        db.execute("DROP TABLE material_events")
        db.execute("PRAGMA user_version=1")
    migrated = KnowledgeStore(store.root)
    event = migrated.history(repository, "rules")[0]
    assert event["reason"] == first["confirmation"]["reason"]
    assert event["at"] == first["confirmation"]["at"]
    assert event["source"] == "legacy_migration"
    assert KnowledgeStore(store.root).history(repository, "rules") == [event]
    assert migrated.read(repository, "rules", version=first["version"])["text"]


def test_confirmation_and_event_are_one_transaction(tmp_path, monkeypatch):
    from repopilot.knowledge import events
    repository, store = make_repo(tmp_path), KnowledgeStore(tmp_path / "runtime")
    first = register((repository, store), confirm=False)
    def fail(*_, **__):
        raise RuntimeError("event write interrupted")
    monkeypatch.setattr(events, "append", fail)
    with pytest.raises(RuntimeError, match="interrupted"):
        store.confirm(repository, "rules", version=first["version"], reason="must be atomic")
    assert store.inspect(repository, "rules")["status"] == "proposed"
    assert store.history(repository, "rules") == []


def test_cli_history_can_find_old_confirmation_after_deletion(tmp_path, capsys):
    import repopilot.cli as cli
    repository, store = make_repo(tmp_path), KnowledgeStore(tmp_path / "runtime")
    first = register((repository, store))
    (repository / "docs/rules.md").unlink()
    store.sync(repository)
    assert cli.main(["knowledge", "history", "--repository", str(repository), "--output", str(store.root),
                     "--id", "rules", "--version", first["version"]]) == 0
    events = json.loads(capsys.readouterr().out)["knowledge"]
    assert [event["event"] for event in events] == ["confirmed", "invalidated"]
    assert events[-1]["reason"] == "source_deleted"


@pytest.mark.parametrize("provider", ["github", "gitlab"])
@pytest.mark.parametrize("current", ["conflict", "missing", "approved"])
def test_reapproved_draft_requires_current_branch_head(tmp_path, provider, current):
    task, settings, root, store, original, api = interrupted_candidate(tmp_path, provider=provider)
    approved = original["reviewed_commit"]
    conflicting = original["base_commit"]
    reads = []
    # 故意让旧观察与当前现场相反，证明发布不信任缓存的匹配／冲突结论。
    remote = {"head": conflicting if current == "approved" else approved}
    def branch_head(*_):
        reads.append(remote["head"])
        return remote["head"]
    api.branch_head = branch_head
    assert publish(original["run_id"], root, api)["status"] == "needs_input"
    state = JobStore(root).get(original["run_id"])
    observation = state["publication"]["reconciliations"][-1]
    assert observation["matches_approved_commit"] is (current != "approved")
    request = next(r for r in state["human_requests"] if r["status"] == "pending")
    record = store.inspect(task.repository, "compatibility")
    store.confirm(task.repository, record["id"], version=record["version"], reason="new material")
    knowledge.refresh(original["run_id"], root, request_id=request["id"], version=request["version"],
        commit=head_commit(attach(state, root).path), reason="recheck current material")
    result = workflow.resume(original["run_id"], settings, root,
        model=FakeModel(responses=[implement_done(), review_approved()]))
    assert result["status"] == "awaiting_approval", result
    approve(JobStore(root).get(original["run_id"]), root)
    remote["head"] = {"conflict": conflicting, "missing": None, "approved": approved}[current]
    if current == "approved":
        assert publish(original["run_id"], root, api)["status"] == "published"
    else:
        code = "remote_branch_conflict" if current == "conflict" else "remote_branch_missing"
        with pytest.raises(ForgeError, match=code):
            publish(original["run_id"], root, api)
        failed = JobStore(root).get(original["run_id"])
        assert failed["status"] == "publication_failed" and failed.get("pull_request") is None
        assert failed["stop_reason"] == code
        assert failed["publication"]["branch"] == state["publication"]["branch"]
        # 人工修复远端后同一个运行可以重试；不把旧的失败观察当永久结论。
        remote["head"] = approved
        assert publish(original["run_id"], root, api)["status"] == "published"
    assert reads[:2] == [observation["branch_head"],
        {"conflict": conflicting, "missing": None, "approved": approved}[current]]
    assert api.posts == 1 and api.pushes == 1
