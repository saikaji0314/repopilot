"""报告不得把已经确认的合并仍展示为未知或未合并，也不能替普通草稿宣布合并。"""

from tests.runtime.test_report import render, sample_state


def test_confirmed_merge_updates_overview_steps_and_scope(tmp_path):
    state = sample_state(tmp_path)
    state.update(status="merged", stop_reason="remote_merge_confirmed", merge={
        "enabled": True, "status": "merged", "reason": "remote_merge_confirmed",
        "commit": "f" * 40, "confirmed_at": "2026-10-29T10:56:23+00:00",
        "policy": {},
    })
    html = render(tmp_path, state=state)
    assert "已合并并确认" in html and "已核对远端主线结果" in html
    assert "已创建 · 已合并" in html
    assert "未知状态" not in html and "尚未合并" not in html
    assert "合并与部署尚未接入" not in html
    assert "软件发行和部署需另行授权" in html


def test_plain_draft_does_not_inherit_other_runs_merge_success(tmp_path):
    html = render(tmp_path)
    assert "已创建 · 尚未合并" in html
    assert "本次交付到草稿 PR，尚未启用自动合并" in html
    assert "已合并并确认" not in html and "已核对远端主线结果" not in html
