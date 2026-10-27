"""固定资料接入实现／审核，人工答复、机器决定、失效及刷新，均不使用真实模型。"""

import json
from pathlib import Path
import subprocess
import sys

import pytest

import repopilot.cli as cli
from repopilot.agents import loop as engine
from repopilot.execution.workspace import run_git
from repopilot.knowledge.documents import KnowledgeFault, Material
from repopilot.knowledge.retrieval import manifest
from repopilot.knowledge.store import KnowledgeStore
from repopilot.orchestration import human, knowledge, workflow
from repopilot.orchestration.state import attach
from repopilot.presentation.report import build
from repopilot.storage.jobs import JobStore, StateError
from tests.runtime.test_human import clarification, reply, write
from tests.runtime.test_review_loop import FakeModel, implement_done, review_approved, setup, turn
from tests.runtime.test_review_loop import review_changes


def policy(tmp_path, *, confirmed=True, required=True, body="# Compatibility\n\n保留旧接口。\n"):
    task, settings, root = setup(tmp_path)
    path = task.repository / "docs/policy.md"
    path.parent.mkdir()
    path.write_text(body, encoding="utf-8")
    run_git(task.repository, "add", "docs/policy.md")
    run_git(task.repository, "commit", "-q", "-m", "policy baseline")
    store = KnowledgeStore(root)
    record = store.register(task.repository, "docs/policy.md",
        Material(id="compatibility", kind="project", title="接口兼容约束", required=required))
    if confirmed:
        record = store.confirm(task.repository, record["id"], version=record["version"], reason="测试负责人确认")
    return task, settings, root, store, record


def test_same_fixed_materials_and_read_tool_reach_both_roles(tmp_path, monkeypatch):
    task, settings, root, store, material = policy(tmp_path)
    payloads, original = {}, engine.run_phase
    def record(**kwargs):
        payloads[kwargs["phase"]] = kwargs["payload"]
        return original(**kwargs)
    monkeypatch.setattr(engine, "run_phase", record)
    read = lambda: turn("read_material", {"material_id": material["id"], "version": material["version"]})
    result = workflow.start(task, settings, root,
        model=FakeModel(responses=[read(), write(), implement_done(), read(), review_approved()]))
    assert result["status"] == "awaiting_approval", result
    implementation, review = (payloads[p]["project_materials"] for p in ("implement", "review"))
    assert implementation["snapshot_id"] == review["snapshot_id"]
    assert implementation["catalog"] == review["catalog"]
    assert "保留旧接口" in implementation["required"][0]["text"]
    assert result["model_calls"] == 5
    events = (root / "runs" / result["run_id"] / "events.jsonl").read_text(encoding="utf-8")
    assert '"name": "read_material"' in events and "保留旧接口" in events


def test_model_decisions_are_versioned_proposals_not_review_authority(tmp_path, monkeypatch):
    task, settings, root = setup(tmp_path)
    response = implement_done()
    response.tool_calls[0]["args"]["decisions"] = [{"summary": "复用既有接口", "reason": "减少调用方变更", "sources": ["app.py:1"]}]
    seen, original = {}, engine.run_phase
    def capture(**kwargs):
        seen[kwargs["phase"]] = kwargs["payload"]
        return original(**kwargs)
    monkeypatch.setattr(engine, "run_phase", capture)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[write(), response, review_approved()]))
    assert result["status"] == "awaiting_approval", result
    reference = result["decision_records"][0]
    store = KnowledgeStore(root)
    assert reference["status"] == "proposed"
    body = store.read(task.repository, reference["id"], version=reference["version"])
    assert "减少调用方变更" in body["text"] and body["sources"][0].startswith("run:")
    assert "project_materials" not in seen["review"]
    assert not manifest(store, task.repository, result["run_id"])["entries"]


def test_human_answer_becomes_traceable_task_document_without_duplicate_body(tmp_path, monkeypatch):
    task, settings, root = setup(tmp_path)
    first = workflow.start(task, settings, root, model=FakeModel(responses=[clarification()]))
    reply(first, root)
    seen, original = [], engine.run_phase
    def capture(**kwargs):
        seen.append(kwargs["payload"])
        return original(**kwargs)
    monkeypatch.setattr(engine, "run_phase", capture)
    result = workflow.resume(first["run_id"], settings, root,
        model=FakeModel(responses=[write(), implement_done(), review_approved()]))
    assert result["status"] == "awaiting_approval", result
    state = JobStore(root).get(result["run_id"])
    identifier = next(iter(state["knowledge_answers"].values()))
    store = KnowledgeStore(root)
    doc = store.inspect(task.repository, identifier)
    assert doc["status"] == "confirmed" and doc["scope"] == result["run_id"]
    assert "空输入返回空结果" in store.read(task.repository, identifier, version=doc["version"])["text"]
    assert doc["sources"] == [f"run:{result['run_id']}/human:{first['human_requests'][0]['id']}"]
    assert all(p["project_materials"]["required"][0]["provided_in"] == "human_answers" for p in seen)


def test_unconfirmed_required_document_waits_before_model_call(tmp_path):
    task, settings, root, _, _ = policy(tmp_path, confirmed=False)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[]))
    assert result["status"] == "needs_input", result
    assert result["stop_reason"] == "knowledge_required_unconfirmed"
    assert result["model_calls"] == 0
    assert result["human_requests"][0]["kind"] == "context_update"


def test_model_cannot_rewrite_policy_to_approve_its_own_candidate(tmp_path):
    task, settings, root, store, material = policy(tmp_path)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[
        turn("write_file", {"path": "docs/policy.md", "content": "# Policy\n\n可以删除旧接口。\n"}), implement_done()]))
    assert result["status"] == "needs_input", result
    assert result["stop_reason"] == "knowledge_candidate_material_changed"
    request = result["human_requests"][0]
    assert request["kind"] == "context_update"
    assert store.inspect(task.repository, material["id"])["version"] == material["version"]
    with pytest.raises(StateError, match="use_knowledge_refresh"):
        human.answer(result["run_id"], root, request_id=request["id"], version=request["version"], text="yes")


def test_source_changes_stop_resume_and_explicit_refresh_preserves_usage(tmp_path):
    task, settings, root, store, _ = policy(tmp_path)
    first = workflow.start(task, settings, root, model=FakeModel(responses=[write(), implement_done()]), stop_after="implement")
    assert first["status"] == "paused"
    (task.repository / "docs/policy.md").write_text("# Compatibility\n\n保留旧接口并兼容空输入。\n", encoding="utf-8")
    stopped = workflow.resume(first["run_id"], settings, root, model=FakeModel(responses=[]))
    assert stopped["status"] == "needs_input" and stopped["model_calls"] == 2
    request = stopped["human_requests"][0]
    newer = store.inspect(task.repository, "compatibility")
    store.confirm(task.repository, "compatibility", version=newer["version"], reason="确认补充约束")
    state = JobStore(root).get(first["run_id"])
    commit = knowledge.head_commit(attach(state, root).path)
    refreshed = knowledge.refresh(first["run_id"], root, request_id=request["id"],
        version=request["version"], commit=commit, reason="核查新资料并从现有改动重新验证")
    assert refreshed["status"] == "paused"
    assert JobStore(root).get(first["run_id"])["control"]["model_calls"] == 2
    done = workflow.resume(first["run_id"], settings, root,
        model=FakeModel(responses=[implement_done(), review_approved()]))
    assert done["status"] == "awaiting_approval", done
    assert done["model_calls"] == 4
    assert done["knowledge_snapshot_id"] != first["knowledge_snapshot_id"]


def test_changes_after_review_block_approval_and_remote_access(tmp_path):
    task, settings, root, store, _ = policy(tmp_path)
    done = workflow.start(task, settings, root,
        model=FakeModel(responses=[write(), implement_done(), review_approved()]))
    (task.repository / "docs/policy.md").unlink()
    with pytest.raises(KnowledgeFault):
        workflow.decide(done["run_id"], root, commit=done["review"]["reviewed_commit"], approved=True, reason="approve")
    # 无人工批准，发布门槛本身已拒绝；已批准的资料变更同样必须在任何远端请求前挡住。
    (task.repository / "docs/policy.md").write_text("# Compatibility\n\n保留旧接口。\n", encoding="utf-8")
    store.sync(task.repository)
    record = store.inspect(task.repository, "compatibility")
    store.confirm(task.repository, "compatibility", version=record["version"], reason="恢复原约束")
    workflow.decide(done["run_id"], root, commit=done["review"]["reviewed_commit"], approved=True, reason="approve")
    (task.repository / "docs/policy.md").write_text("# Changed\n\nDifferent rule.\n", encoding="utf-8")
    state = JobStore(root).get(done["run_id"])
    state["issue"] = {"url": "https://github.com/owner/repo/issues/1", "repository": "owner/repo", "number": 1}
    state["approval_digest"] = workflow.approval_digest(state)
    state["approval"]["digest"] = state["approval_digest"]
    JobStore(root).save(state)
    class RejectRemote:
        def __getattr__(self, name):
            raise AssertionError("stale materials must not reach remote: " + name)
    from repopilot.delivery.forge import publish
    with pytest.raises(KnowledgeFault):
        publish(done["run_id"], root, RejectRemote())


def test_read_tool_refuses_material_outside_frozen_manifest(tmp_path):
    task, settings, root, _, _ = policy(tmp_path)
    first = workflow.start(task, settings, root, model=FakeModel(responses=[]), stop_after="prepare")
    state = JobStore(root).get(first["run_id"])
    reader = knowledge.tools(state, root)[0]
    denied = reader.invoke({"material_id": "foreign", "version": "0" * 64})
    assert denied == {"ok": False, "error": "knowledge_reference_not_in_snapshot"}


def test_required_text_is_not_silently_truncated(tmp_path):
    task, settings, root, _, _ = policy(tmp_path, body="# Rules\n" + "约束内容\n" * 210)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[]))
    assert result["status"] == "needs_input", result
    assert result["stop_reason"] == "knowledge_required_context_too_large"
    assert result["model_calls"] == 0


def test_missing_knowledge_store_does_not_drop_confirmed_constraints(tmp_path):
    task, settings, root, _, _ = policy(tmp_path)
    first = workflow.start(task, settings, root, model=FakeModel(responses=[]), stop_after="prepare")
    (root / "knowledge.sqlite3").unlink()
    result = workflow.resume(first["run_id"], settings, root, model=FakeModel(responses=[]))
    assert result["status"] == "needs_input" and result["stop_reason"] == "knowledge_store_unavailable"


def test_refresh_does_not_accept_wrong_request_or_commit(tmp_path):
    task, settings, root, store, material = policy(tmp_path, confirmed=False)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[]))
    request = result["human_requests"][0]
    with pytest.raises(KnowledgeFault, match="request_mismatch"):
        knowledge.refresh(result["run_id"], root, request_id=request["id"], version="wrong", commit="0" * 40, reason="check")
    with pytest.raises(KnowledgeFault, match="commit_mismatch"):
        knowledge.refresh(result["run_id"], root, request_id=request["id"], version=request["version"], commit="0" * 40, reason="check")


def test_cli_register_confirm_search_in_new_process_need_no_credentials(tmp_path, monkeypatch, capsys):
    task, _, root = setup(tmp_path)
    (task.repository / "policy.md").write_text("# Policy\n\n保留旧接口。\n", encoding="utf-8")
    def unavailable(*_, **__):
        pytest.fail("资料命令不应加载API凭证")
    monkeypatch.setattr(cli, "load_settings", unavailable)
    monkeypatch.setattr(cli, "open_forge", unavailable)
    base = ["--repository", str(task.repository), "--output", str(root)]
    assert cli.main(["knowledge", "register", *base, "--id", "rule", "--path", "policy.md", "--title", "旧接口约束"]) == 0
    record = json.loads(capsys.readouterr().out)["knowledge"]
    assert cli.main(["knowledge", "confirm", *base, "--id", "rule", "--version", record["version"], "--reason", "checked"]) == 0
    capsys.readouterr()
    result = subprocess.run([sys.executable, "-m", "repopilot.cli", "knowledge", "search", *base, "--query", "旧接口"],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["knowledge"]["entries"][0]["id"] == "rule"


def test_report_displays_material_versions_and_pending_context_update(tmp_path):
    task, settings, root, _, material = policy(tmp_path)
    first = workflow.start(task, settings, root, model=FakeModel(responses=[]), stop_after="prepare")
    state = JobStore(root).get(first["run_id"])
    knowledge.pause(state, root, "knowledge_snapshot_changed")
    html = build(state, root / "runs" / first["run_id"], Path.cwd())
    assert material["version"] in html and "docs/policy.md" in html
    assert "repopilot knowledge refresh" in html


def test_refresh_keeps_pending_rework_as_the_same_modification_round(tmp_path):
    task, settings, root, store, _ = policy(tmp_path)
    first = workflow.start(task, settings, root,
        model=FakeModel(responses=[write(), implement_done(), review_changes()]), stop_after="review")
    assert JobStore(root).get(first["run_id"])["rework_pending"] is True
    (task.repository / "docs/policy.md").write_text("# Updated\n\n保留旧接口并补充回归。\n", encoding="utf-8")
    stopped = workflow.resume(first["run_id"], settings, root, model=FakeModel(responses=[]))
    record = store.inspect(task.repository, "compatibility")
    store.confirm(task.repository, "compatibility", version=record["version"], reason="updated")
    state = JobStore(root).get(first["run_id"])
    request = next(r for r in stopped["human_requests"] if r["status"] == "pending")
    knowledge.refresh(first["run_id"], root, request_id=request["id"], version=request["version"],
        commit=knowledge.head_commit(attach(state, root).path), reason="check updated policy")
    assert JobStore(root).get(first["run_id"])["phase"] == "rework"
    done = workflow.resume(first["run_id"], settings, root,
        model=FakeModel(responses=[write(3), implement_done(), review_approved()]))
    assert done["status"] == "awaiting_approval" and done["review"]["rework_attempts"] == 1


def test_refresh_refuses_to_change_the_original_task(tmp_path):
    task, settings, root, _, _ = policy(tmp_path, confirmed=False)
    waiting = workflow.start(task, settings, root, model=FakeModel(responses=[]))
    state = JobStore(root).get(waiting["run_id"])
    state["task"]["request"] = "different business goal"
    JobStore(root).save(state)
    request = waiting["human_requests"][0]
    with pytest.raises(KnowledgeFault, match="task_changed"):
        knowledge.refresh(waiting["run_id"], root, request_id=request["id"], version=request["version"],
            commit=knowledge.head_commit(attach(state, root).path), reason="refresh")


def test_corrupt_index_pauses_instead_of_running_without_rules(tmp_path):
    task, settings, root, _, _ = policy(tmp_path)
    first = workflow.start(task, settings, root, model=FakeModel(responses=[]), stop_after="prepare")
    (root / "knowledge.sqlite3").write_bytes(b"not a sqlite database")
    stopped = workflow.resume(first["run_id"], settings, root, model=FakeModel(responses=[]))
    assert stopped["status"] == "needs_input" and stopped["model_calls"] == 0
    assert stopped["stop_reason"] == "knowledge_store_unavailable"


def test_material_read_observation_keeps_version_and_source():
    from repopilot.agents.context import remember_observation
    progress = {"phase": "implement"}
    remember_observation(progress, "read_material", {"material_id": "rule", "version": "v1"},
        {"ok": True, "path": "policy.md", "text": "fixed text", "start_line": 1, "end_line": 1})
    observation = progress["observations"][0]
    assert observation["version"] == "v1" and observation["path"] == "policy.md"
    assert observation["excerpt"] == "fixed text"


def test_missing_archived_body_after_review_cannot_be_approved(tmp_path):
    task, settings, root, _, _ = policy(tmp_path)
    done = workflow.start(task, settings, root,
        model=FakeModel(responses=[write(), implement_done(), review_approved()]))
    assert done["status"] == "awaiting_approval"
    next(root.glob("knowledge/**/versions/*.md")).unlink()
    with pytest.raises(KnowledgeFault, match="snapshot_unavailable"):
        workflow.decide(done["run_id"], root, commit=done["review"]["reviewed_commit"], approved=True, reason="approve")
    paused = workflow.resume(done["run_id"], settings, root, model=FakeModel(responses=[]))
    assert paused["status"] == "needs_input"
    assert paused["human_requests"][-1]["kind"] == "context_update"


def test_large_optional_material_does_not_force_context_overflow(tmp_path):
    task, settings, root, _, _ = policy(tmp_path, required=False, body="optional " * 1200)
    result = workflow.start(task, settings, root,
        model=FakeModel(responses=[write(), implement_done(), review_approved()]))
    assert result["status"] == "awaiting_approval", result


def test_resume_repairs_answer_export_interrupted_before_job_checkpoint(tmp_path):
    task, settings, root = setup(tmp_path)
    first = workflow.start(task, settings, root, model=FakeModel(responses=[clarification()]))
    reply(first, root)
    unsaved = JobStore(root).get(first["run_id"])
    assert knowledge.sync_answers(unsaved, root)
    # 模拟资料文件和索引已保存，但任务检查点尚未提交就退出。
    assert not JobStore(root).get(first["run_id"]).get("knowledge_answers")
    done = workflow.resume(first["run_id"], settings, root,
        model=FakeModel(responses=[write(), implement_done(), review_approved()]))
    assert done["status"] == "awaiting_approval", done
    state = JobStore(root).get(first["run_id"])
    assert len(state["knowledge_answers"]) == 1
    assert len(state["knowledge_snapshot"]["entries"]) == 1


def test_refresh_retains_old_manifest_and_review_binding(tmp_path):
    task, settings, root, store, old = policy(tmp_path)
    done = workflow.start(task, settings, root, model=FakeModel(responses=[write(), implement_done(), review_approved()]))
    original = JobStore(root).get(done["run_id"])
    old_snapshot = original["knowledge_snapshot"]
    assert original["history"][-1]["knowledge_snapshot_id"] == old_snapshot["snapshot_id"]
    (task.repository / "docs/policy.md").write_text("# Updated\n\n保留旧接口与新的兼容要求。\n", encoding="utf-8")
    paused = workflow.resume(done["run_id"], settings, root, model=FakeModel(responses=[]))
    record = store.inspect(task.repository, "compatibility")
    store.confirm(task.repository, "compatibility", version=record["version"], reason="new confirmed policy")
    state = JobStore(root).get(done["run_id"])
    request = next(r for r in paused["human_requests"] if r["status"] == "pending")
    knowledge.refresh(done["run_id"], root, request_id=request["id"], version=request["version"],
        commit=knowledge.head_commit(attach(state, root).path), reason="accept refreshed material")
    state = JobStore(root).get(done["run_id"])
    assert state["knowledge_history"][old_snapshot["snapshot_id"]] == old_snapshot
    assert state["knowledge_snapshot"]["snapshot_id"] != old_snapshot["snapshot_id"]
    assert state["knowledge_history"][old_snapshot["snapshot_id"]]["entries"][0]["version"] == old["version"]


def test_machine_decision_records_actual_code_and_material_snapshot(tmp_path):
    task, settings, root, _, _ = policy(tmp_path)
    report = implement_done()
    report.tool_calls[0]["args"]["decisions"] = [{"summary": "保留旧接口", "reason": "按已确认约束实现", "sources": ["docs/policy.md:3"]}]
    done = workflow.start(task, settings, root, model=FakeModel(responses=[write(), report, review_approved()]))
    reference = done["decision_records"][0]
    state = JobStore(root).get(done["run_id"])
    assert reference["knowledge_snapshot_id"] in state["knowledge_history"]
    assert len(reference["code_snapshot"]) == 64
    assert reference["modification"] == 0
