"""格式错误的审核需显式恢复；固定现场、费用和已完成视角不可丢失。"""

import pytest

from repopilot.orchestration import workflow
from repopilot.storage.jobs import JobStore, StateError
from tests.runtime.test_dual_review import (
    Scripted, finding, implementation, resume, review, setup, start,
)


def failed_review(tmp_path):
    task, settings, root = setup(tmp_path)
    scripts = Scripted([{
        "correctness": review(findings=[finding(basis="check", reference="")]),
        "context": review(),
    }])
    result = start(task, settings, root, scripts, implementation())
    assert result["status"] == "review_inconclusive"
    assert result["stop_reason"] == "invalid_review_report"
    return result["run_id"], settings, root


def test_explicit_recovery_only_completes_missing_review_without_resetting_budget(tmp_path):
    run_id, settings, root = failed_review(tmp_path)
    store = JobStore(root)
    before = store.get(run_id)
    scripts = Scripted([{"correctness": review(), "context": review()}])
    stopped = resume(run_id, settings, root, scripts, [])
    assert stopped["status"] == "review_inconclusive" and scripts.calls == []
    commit = before["reviewed_commit"]
    workflow.recover(run_id, root, commit=commit, reason="核对原固定提交，补跑无效报告")
    recovered = store.get(run_id)
    assert recovered["control"] == before["control"]
    assert recovered["settings"] == before["settings"]
    assert recovered["review_pending"] == before["review_pending"]
    assert recovered["recovery"]["from_stop_reason"] == "invalid_review_report"
    assert recovered["approval"] is None
    done = resume(run_id, settings, root, scripts, [])
    assert done["status"] == "awaiting_approval"
    assert scripts.calls == [("correctness", 1)], "合格的另一视角必须复用"
    assert store.get(run_id)["reviewed_commit"] == commit


@pytest.mark.parametrize("change", ["wrong_commit", "dirty_tree", "unable_to_review"])
def test_review_recovery_rejects_other_version_changes_or_business_inconclusive(tmp_path, change):
    run_id, _, root = failed_review(tmp_path)
    store = JobStore(root)
    before = store.get(run_id)
    commit = before["reviewed_commit"]
    if change == "wrong_commit":
        commit = "0" * 40
    elif change == "dirty_tree":
        (workflow.attach(before, root).repository() / "app.py").write_text("external change\n")
    else:
        before["stop_reason"] = "review_unable_to_review"
        store.save(before)
    with pytest.raises(StateError):
        workflow.recover(run_id, root, commit=commit, reason="核对现场")
    assert store.get(run_id) == before
