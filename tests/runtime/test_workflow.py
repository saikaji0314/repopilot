"""持久化边界与审批版本约束，使用确定性模型和真实本地 Git。"""

import pytest

import repopilot.orchestration.workflow as workflow
from repopilot.storage.jobs import JobStore, StateError, job_lock
from repopilot.execution.workspace import WorkspaceError, head_commit
from tests.runtime.test_review_loop import setup, FakeModel, turn, implement_done, review_approved


def implementation():
    return [turn("write_file", {"path": "app.py", "content": "value = 2\n"}), implement_done()]


@pytest.mark.parametrize("boundary", ["prepare", "implement", "anchor", "verify"])
def test_resume_completed_stage_does_not_replay_model(tmp_path, boundary):
    task, settings, root = setup(tmp_path)
    model = FakeModel(responses=[*implementation(), *review_approved()])
    paused = workflow.start(task, settings, root, model=model, stop_after=boundary)
    assert paused["status"] == "paused", paused
    resumed = workflow.resume(paused["run_id"], settings, root, model=model)
    assert resumed["status"] == "awaiting_approval", resumed
    assert resumed["model_calls"] == 4
    again = workflow.resume(paused["run_id"], settings, root, model=model)
    assert again["model_calls"] == 4
    assert again["review"]["reviewed_commit"] == resumed["review"]["reviewed_commit"]


def candidate(tmp_path):
    task, settings, root = setup(tmp_path)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[*implementation(), *review_approved()]))
    assert result["status"] == "awaiting_approval", result
    return result, root


def test_approval_requires_exact_reviewed_commit(tmp_path):
    result, root = candidate(tmp_path)
    with pytest.raises(StateError, match="stale_approval"):
        workflow.decide(result["run_id"], root, commit="0" * 40, approved=True, reason="核对")
    approved = workflow.decide(result["run_id"], root, commit=result["review"]["reviewed_commit"], approved=True, reason="核对")
    assert approved["status"] == "approved"
    assert JobStore(root).get(result["run_id"])["approval"]["commit"] == result["review"]["reviewed_commit"]


def test_changed_tree_invalidates_human_approval(tmp_path):
    result, root = candidate(tmp_path)
    (workflow.attach(JobStore(root).get(result["run_id"]), root).path / "app.py").write_text("changed\n")
    with pytest.raises(WorkspaceError, match="reviewed_tree_changed"):
        workflow.decide(result["run_id"], root, commit=result["review"]["reviewed_commit"], approved=True, reason="核对")


def test_in_flight_phase_requires_explicit_recovery(tmp_path):
    task, settings, root = setup(tmp_path)
    first = workflow.start(task, settings, root, model=FakeModel(responses=implementation()), stop_after="prepare")
    store = JobStore(root)
    state = store.get(first["run_id"])
    state["in_flight"] = "implement"
    state["control"]["model_calls"] = 4
    store.save(state)
    result = workflow.resume(first["run_id"], settings, root, model=FakeModel(responses=[]))
    assert result["status"] == "recovery_required"
    commit = head_commit(workflow.attach(state, root).path)
    workflow.recover(first["run_id"], root, commit=commit, reason="现场核对无改动")
    result = workflow.resume(first["run_id"], settings, root,
        model=FakeModel(responses=[*implementation(), *review_approved()]))
    assert result["status"] == "awaiting_approval", result
    assert result["model_calls"] == 8


def test_resume_rejects_changes_since_checkpoint(tmp_path):
    task, settings, root = setup(tmp_path)
    result = workflow.start(task, settings, root, model=FakeModel(responses=implementation()), stop_after="implement")
    state = JobStore(root).get(result["run_id"])
    (workflow.attach(state, root).path / "app.py").write_text("value = 999\n")
    result = workflow.resume(result["run_id"], settings, root, model=FakeModel(responses=[]))
    assert result["stop_reason"] == "checkpoint_tree_changed"


def test_cas_and_lock_prevent_overlapping_writers(tmp_path):
    store = JobStore(tmp_path)
    state = store.create({"run_id": "job"})
    stale = store.get("job")
    store.save(state)
    with pytest.raises(StateError, match="checkpoint_conflict"):
        store.save(stale)
    with job_lock(tmp_path, "job"):
        with pytest.raises(StateError, match="job_is_running"):
            with job_lock(tmp_path, "job"):
                pass


def test_explicit_retry_preserves_old_attempt_and_shared_cost(tmp_path):
    task, settings, root = setup(tmp_path, max_model_calls=1)
    first = workflow.start(task, settings, root, model=FakeModel(responses=implementation()))
    assert first["status"] == "limit_reached"
    second = workflow.retry(first["run_id"], settings, root, reason="只验证新尝试，不抹去失败",
        overrides={"max_model_calls": 4}, model=FakeModel(responses=[*implementation(), *review_approved()]))
    assert second["status"] == "awaiting_approval", second
    assert second["run_id"] != first["run_id"]
    store = JobStore(root)
    assert store.get(first["run_id"])["status"] == "limit_reached"
    assert store.get(second["run_id"])["retry_of"] == first["run_id"]
    assert second["budget"]["calls"] == 5
    assert second["budget"]["accounted_cny"] > second["cost"]["accounted_cny"]


def test_recover_interrupted_worktree_creation(tmp_path, monkeypatch):
    from repopilot.execution.workspace import Workspace
    task, settings, root = setup(tmp_path)
    original = Workspace.prepare
    def interrupt(self):
        raise KeyboardInterrupt()
    monkeypatch.setattr(Workspace, "prepare", interrupt)
    result = workflow.start(task, settings, root, model=FakeModel(responses=[]))
    assert result["status"] == "interrupted" and result["phase"] == "prepare"
    monkeypatch.setattr(Workspace, "prepare", original)
    workflow.recover(result["run_id"], root, commit=head_commit(task.repository), reason="创建前中断，核对基线")
    resumed = workflow.resume(result["run_id"], settings, root,
        model=FakeModel(responses=[*implementation(), *review_approved()]))
    assert resumed["status"] == "awaiting_approval", resumed


def test_independent_check_must_not_mutate_reviewed_tree(tmp_path):
    from repopilot.core.config import Check
    task, settings, root = setup(tmp_path)
    task.checks['unit'] = Check(argv=['{python}', '-c', "from pathlib import Path;Path('app.py').write_text('mutated\\n')"])
    result = workflow.start(task, settings, root, model=FakeModel(responses=[*implementation(), *review_approved()]))
    assert result["status"] == "workspace_error", result
    assert result["stop_reason"] == "reviewed_tree_changed"
    assert result["review"]["verdict"] is None


def test_remote_pinned_base_can_resume_without_moving_source_head(tmp_path, monkeypatch):
    from repopilot.execution.workspace import Workspace, source_snapshot
    task, settings, root = setup(tmp_path)
    before = source_snapshot(task.repository)
    remote = Workspace(task.repository, tmp_path / 'remote-worktrees').prepare()
    (remote.path / 'remote.txt').write_text('remote base\n')
    commit = remote.commit('feat: synthetic upstream')['commit']
    state = workflow.create_job(task, settings, root, base_commit=commit)
    original = Workspace.prepare
    monkeypatch.setattr(Workspace, 'prepare', lambda self: (_ for _ in ()).throw(KeyboardInterrupt()))
    first = workflow.resume(state['run_id'], settings, root, model=FakeModel(responses=[]))
    assert first['status'] == 'interrupted'
    monkeypatch.setattr(Workspace, 'prepare', original)
    workflow.recover(state['run_id'], root, commit=commit, reason='remote base 创建前中断')
    result = workflow.resume(state['run_id'], settings, root,
        model=FakeModel(responses=[*implementation(), *review_approved()]))
    assert result['status'] == 'awaiting_approval', result
    assert source_snapshot(task.repository) == before
    assert (workflow.attach(JobStore(root).get(state['run_id']), root).path / 'remote.txt').exists()
