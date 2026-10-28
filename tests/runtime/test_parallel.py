"""M4并行子任务、独立worktree、串行整合与槽位约束；模型和整合Agent为确定性替身。"""

from pathlib import Path
import threading

import pytest

from repopilot.agents import loop as engine
from repopilot.agents.reports import ImplementationReport
from repopilot.agents.trace import Trace
from repopilot.core.config import RuntimeSettings
from repopilot.execution.workspace import EditBudget, head_commit
from repopilot.orchestration import parallel, workflow
from repopilot.orchestration.slots import SlotFault, SlotManager
from repopilot.storage.jobs import JobStore
from tests.runtime.test_planning import plan_ready, subtask
from tests.runtime.test_review_loop import FakeModel, review_approved, setup


def report(name="done"):
    return {"status": "implemented", "summary": name,
            "evidence": [{"kind": "check", "name": "unit", "exit_code": 0,
                          "observation": "登记检查通过"}],
            "next_steps": [], "questions": [], "decisions": []}


def worker_result(workspace, subtask_id, base_commit):
    (Path(workspace.path) / f"{subtask_id}.txt").write_text(f"{subtask_id}\n", encoding="utf-8")
    commit = workspace.commit(f"subtask {subtask_id}")["commit"]
    return {"report": report(f"done {subtask_id}"), "commit": commit,
            "worktree": str(workspace.path), "base_commit": base_commit, "dirty": True,
            "started_at": "synthetic-start", "finished_at": "synthetic-finish"}


def test_two_independent_subtasks_start_in_parallel_and_integrate(tmp_path, monkeypatch):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    subtasks = [subtask("first"), subtask("second")]
    barrier = threading.Barrier(2)
    starts, active, peak = [], 0, 0
    lock = threading.Lock()

    def fake(**kwargs):
        nonlocal active, peak
        subtask_id = kwargs["payload"]["subtask"]["id"]
        base = kwargs["payload"]["parallel"]["base_commit"]
        with lock:
            active += 1
            peak = max(peak, active)
            starts.append((subtask_id, threading.current_thread().name, base))
        barrier.wait(timeout=10)
        result = worker_result(kwargs["workspace"], subtask_id, base)
        with lock:
            active -= 1
        return result
    monkeypatch.setattr(parallel, "run_subtask", fake)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[
        plan_ready(subtasks, split="parallel"), review_approved()]))
    assert result["status"] == "awaiting_approval", result
    assert peak == 2, "两个独立子任务应真正并行启动"
    assert result["plan_status"]["completed"] == 2
    assert [item["status"] for item in result["integrations"]] == ["integrated", "integrated"]
    assert all(item["resolution"] == "clean" for item in result["integrations"])
    assert result["repository_unchanged"] is True
    state = JobStore(root).get(result["run_id"])
    parent = workflow.attach(state, root)
    assert (parent.path / "first.txt").exists() and (parent.path / "second.txt").exists()
    assert {item["subtask"] for item in result["parallel_runs"]} == {"first", "second"}
    worktrees = {item["worktree"] for item in result["parallel_runs"]}
    assert len(worktrees) == 2, "每个子任务必须有独立worktree"


def test_dependent_subtask_waits_for_integrated_predecessor(tmp_path, monkeypatch):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    subtasks = [subtask("first"), subtask("second"), subtask("third", depends_on=["first"])]
    starts = []

    def fake(**kwargs):
        subtask_id = kwargs["payload"]["subtask"]["id"]
        base = kwargs["payload"]["parallel"]["base_commit"]
        worktree = Path(kwargs["workspace"].path)
        starts.append({"id": subtask_id, "base": base,
                       "first_exists": (worktree / "first.txt").exists()})
        return worker_result(kwargs["workspace"], subtask_id, base)
    monkeypatch.setattr(parallel, "run_subtask", fake)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[
        plan_ready(subtasks, split="parallel"), review_approved()]))
    assert result["status"] == "awaiting_approval", result
    third = next(item for item in starts if item["id"] == "third")
    integrated = {item["integrated_commit"] for item in result["integrations"]}
    assert third["first_exists"] is True, "依赖子任务必须从已整合前置结果开始"
    assert third["base"] != starts[0]["base"]
    assert third["base"] in integrated, "依赖子任务的基线必须是一个已整合结果"


def test_conflict_is_resolved_by_controlled_integration_step(tmp_path, monkeypatch):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    subtasks = [subtask("first"), subtask("second")]
    conflicts = []

    def fake(**kwargs):
        subtask_id = kwargs["payload"]["subtask"]["id"]
        base = kwargs["payload"]["parallel"]["base_commit"]
        (Path(kwargs["workspace"].path) / "app.py").write_text(f"value = {subtask_id}\n", encoding="utf-8")
        commit = kwargs["workspace"].commit(f"subtask {subtask_id}")["commit"]
        return {"report": report(f"done {subtask_id}"), "commit": commit,
                "worktree": str(kwargs["workspace"].path), "base_commit": base, "dirty": True,
                "started_at": "synthetic-start", "finished_at": "synthetic-finish"}

    def resolve(**kwargs):
        conflicts.extend(kwargs["payload"]["conflict"]["files"])
        (Path(kwargs["workspace"].path) / "app.py").write_text("value = first\nvalue = second\n",
                                                               encoding="utf-8")
        return {"status": "resolved", "summary": "保留双方", "decisions": [], "questions": []}
    monkeypatch.setattr(parallel, "run_subtask", fake)
    monkeypatch.setattr(parallel, "resolve_conflict", resolve)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[
        plan_ready(subtasks, split="parallel"), review_approved()]))
    assert result["status"] == "awaiting_approval", result
    assert conflicts == ["app.py"]
    conflict_record = next(item for item in result["integrations"]
                           if item["resolution"] == "model")
    assert conflict_record["status"] == "integrated"
    state = JobStore(root).get(result["run_id"])
    parent = workflow.attach(state, root)
    assert (parent.path / "app.py").read_text(encoding="utf-8") == "value = first\nvalue = second\n"


def test_conflict_can_pause_for_human_decision_without_delivery(tmp_path, monkeypatch):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    subtasks = [subtask("first"), subtask("second")]

    def fake(**kwargs):
        subtask_id = kwargs["payload"]["subtask"]["id"]
        base = kwargs["payload"]["parallel"]["base_commit"]
        (Path(kwargs["workspace"].path) / "app.py").write_text(f"value = {subtask_id}\n", encoding="utf-8")
        commit = kwargs["workspace"].commit(f"subtask {subtask_id}")["commit"]
        return {"report": report(f"done {subtask_id}"), "commit": commit,
                "worktree": str(kwargs["workspace"].path), "base_commit": base, "dirty": True,
                "started_at": "synthetic-start", "finished_at": "synthetic-finish"}

    monkeypatch.setattr(parallel, "run_subtask", fake)
    monkeypatch.setattr(parallel, "resolve_conflict", lambda **_: {
        "status": "needs_clarification", "summary": "两个接口互斥",
        "decisions": [], "questions": ["保留哪个接口？"]})
    result = workflow.start(task, settings, root, model=FakeModel(responses=[
        plan_ready(subtasks, split="parallel")]))
    assert result["status"] == "needs_input", result
    assert result["plan_status"]["state"] == "blocked"
    pending = [item for item in result["human_requests"]
               if item["status"] == "pending" and item["kind"] == "clarification"]
    assert pending and pending[-1]["blocking"][0]["phase"] == "integrate"
    state = JobStore(root).get(result["run_id"])
    parent = workflow.attach(state, root)
    content = (parent.path / "app.py").read_text(encoding="utf-8")
    assert "value =" in content and "<<<<<<<" not in content


def test_one_slot_forces_serial_execution(tmp_path, monkeypatch):
    task, settings, root = setup(tmp_path, plan_enabled=True, max_implementation_slots=1)
    subtasks = [subtask("first"), subtask("second")]
    active, peak = 0, 0
    lock = threading.Lock()

    def fake(**kwargs):
        nonlocal active, peak
        subtask_id = kwargs["payload"]["subtask"]["id"]
        base = kwargs["payload"]["parallel"]["base_commit"]
        with lock:
            active += 1
            peak = max(peak, active)
        result = worker_result(kwargs["workspace"], subtask_id, base)
        with lock:
            active -= 1
        return result
    monkeypatch.setattr(parallel, "run_subtask", fake)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[
        plan_ready(subtasks, split="parallel"), review_approved()]))
    assert result["status"] == "awaiting_approval"
    assert peak == 1
    assert [item["status"] for item in result["integrations"]] == ["integrated", "integrated"]


def test_parallel_clarification_pauses_without_integrating_other_worker(tmp_path, monkeypatch):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    subtasks = [subtask("first"), subtask("second")]
    started = []

    def fake(**kwargs):
        subtask_id = kwargs["payload"]["subtask"]["id"]
        base = kwargs["payload"]["parallel"]["base_commit"]
        started.append(subtask_id)
        if subtask_id == "first":
            return {"report": {"status": "needs_clarification", "summary": "需要确认",
                               "evidence": [], "next_steps": [], "questions": ["选哪个方案？"],
                               "decisions": []}, "commit": None,
                    "worktree": str(kwargs["workspace"].path), "base_commit": base,
                    "dirty": False, "started_at": "s", "finished_at": "f"}
        threading.Event().wait(0.2)
        return worker_result(kwargs["workspace"], subtask_id, base)
    monkeypatch.setattr(parallel, "run_subtask", fake)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[
        plan_ready(subtasks, split="parallel")]))
    assert result["status"] == "needs_input", result
    assert "first" in started and "second" in started
    pending = [item for item in result["human_requests"]
               if item["status"] == "pending" and item["kind"] == "clarification"]
    assert pending and pending[-1]["blocking"][0]["subtask"] == "first"
    assert not result["integrations"], "暂停时不能把未处理结果当整合完成"
    assert {item["status"] for item in result["parallel_runs"]} >= {"paused", "interrupted"}


def test_parallel_report_shows_source_and_integrated_commits(tmp_path, monkeypatch):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    subtasks = [subtask("first"), subtask("second")]
    monkeypatch.setattr(parallel, "run_subtask", lambda **kwargs: worker_result(
        kwargs["workspace"], kwargs["payload"]["subtask"]["id"],
        kwargs["payload"]["parallel"]["base_commit"]))
    result = workflow.start(task, settings, root, model=FakeModel(responses=[
        plan_ready(subtasks, split="parallel"), review_approved()]))
    from repopilot.presentation.report import build
    html = build(JobStore(root).get(result["run_id"]), root / "runs" / result["run_id"], Path.cwd())
    assert "内部整合" in html and "来源提交" in html and "整合提交" in html
    assert result["integrations"][0]["source_commit"][:10] in html
    assert result["integrations"][0]["integrated_commit"][:10] in html


def test_parallel_combined_candidate_is_approved_as_one_commit(tmp_path, monkeypatch):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    subtasks = [subtask("first"), subtask("second")]
    monkeypatch.setattr(parallel, "run_subtask", lambda **kwargs: worker_result(
        kwargs["workspace"], kwargs["payload"]["subtask"]["id"],
        kwargs["payload"]["parallel"]["base_commit"]))
    result = workflow.start(task, settings, root, model=FakeModel(responses=[
        plan_ready(subtasks, split="parallel"), review_approved()]))
    assert result["status"] == "awaiting_approval"
    approved = workflow.decide(result["run_id"], root, commit=result["review"]["reviewed_commit"],
                               approved=True, reason="核对组合差异与检查")
    assert approved["status"] == "approved"
    state = JobStore(root).get(result["run_id"])
    assert state["approval"]["commit"] == result["review"]["reviewed_commit"]
    assert state["integrations"] and all(item["status"] == "integrated" for item in state["integrations"])


def test_slot_manager_is_shared_and_released(tmp_path):
    manager = SlotManager(tmp_path, limit=2)
    first = manager.acquire("run-a:task-1", timeout=1)
    second = manager.acquire("run-a:task-2", timeout=1)
    with pytest.raises(SlotFault, match="implementation_slots_busy"):
        manager.acquire("run-b:task-1", timeout=0.3)
    assert manager.snapshot()["used"] == 2
    manager.release(first)
    manager.release(second)
    third = manager.acquire("run-b:task-1", timeout=1)
    manager.release(third)
    assert manager.snapshot()["used"] == 0


def test_forked_control_shares_quota_but_keeps_progress_local(tmp_path):
    settings = RuntimeSettings(api_key="synthetic", plan_enabled=False)
    trace = Trace(tmp_path / "run", "synthetic-secret")
    control = engine.RunControl(settings, None, trace, "synthetic-run")
    worker = control.fork("implement")
    assert worker.progress is not control.progress
    worker.model_calls = 2
    worker.checks.append({"name": "unit", "exit_code": 0})
    assert control.model_calls == 2 and control.checks[0]["name"] == "unit"
    worker.deadline = 10
    assert control.deadline == 10
    assert worker.trace.on_event is None and control.trace.on_event is None


def test_parallel_worker_checks_local_changes_and_commits(tmp_path, monkeypatch):
    task, settings, root = setup(tmp_path, plan_enabled=True)
    base = head_commit(task.repository)
    workspace = parallel.create_worktree(task, root, "run", "worker", base)
    (root / "runs" / "run").mkdir(parents=True, exist_ok=True)
    trace = Trace(root / "runs" / "run", "synthetic-secret")
    control = engine.RunControl(settings, None, trace, "run").fork("implement")

    def fake_phase(**kwargs):
        (Path(workspace.path) / "worker.txt").write_text("done\n", encoding="utf-8")
        return ImplementationReport.model_validate({
            "status": "implemented", "summary": "worker完成",
            "evidence": [{"kind": "check", "name": "unit", "exit_code": 0,
                          "observation": "synthetic"}],
            "next_steps": [], "questions": [], "decisions": []})
    monkeypatch.setattr(engine, "run_phase", fake_phase)
    result = parallel.run_subtask(task=task, settings=settings, control=control,
        trace=trace, workspace=workspace, payload={"subtask": {"id": "worker"}},
        model=None, edits=EditBudget(settings.max_edits), material_tools=[])
    assert result["report"]["status"] == "implemented"
    assert result["commit"] and result["dirty"] is True
    assert (workspace.path / "worker.txt").exists()
    assert head_commit(task.repository) == base, "子任务不能改写原仓库HEAD"
