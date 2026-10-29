import json
import re
from pathlib import Path

import pytest

import repopilot.presentation.report as report


def sample_state(root: Path) -> dict:
    return {
        "run_id": "demo-run",
        "status": "published",
        "phase": "approval",
        "stop_reason": "draft_pr_created",
        "issue": {"repository": "example/repo", "number": 7,
                  "url": "https://example.test/issues/7",
                  "title": "标题 <script>alert(1)</script>"},
        "control": {"model_calls": 12, "tool_calls": 30, "accounted_cny": 0.42},
        "edits": {"edits": 3, "edit_limit": 12, "edited_files": ["a.py"]},
        "verified": [{"name": "unit", "exit_code": 0, "timed_out": False,
                      "output_tail": "Ran 5 tests\nOK " + str(root / "worktrees")}],
        "history": [
            {"attempt": 0, "commit": "abc123def456", "verdict": "changes_requested",
             "summary": "需要补充测试",
             "findings": [{"path": "a.py", "start_line": 9, "severity": "major",
                           "observation": "缺边界用例", "suggestion": "补一条空输入用例"}]},
            {"attempt": 1, "commit": "def456abc123", "verdict": "approved",
             "summary": "已满足需求", "findings": []},
        ],
        "review_report": {"verdict": "approved", "findings": []},
        "approval": {"approved": True, "commit": "e3ac2677aa", "digest": "deadbeef",
                     "reason": "已核对候选说明", "at": "2026-10-16T15:02:06+00:00"},
        "pull_request": {"url": "https://example.test/pull/2", "commit": "e3ac2677aa",
                         "at": "2026-10-16T15:02:17+00:00"},
        "report": {"summary": "实现完成",
                   "evidence": [{"kind": "file", "path": "a.py", "start_line": 1, "observation": "改动"}]},
    }


def write_run(run_dir: Path, *, diff: str = "--- a/a.py\n+++ b/a.py\n+print('hi')\n") -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "changes.json").write_text(json.dumps({
        "base_commit": "b" * 40, "head_commit": "c" * 40, "changed_files": 1,
        "additions": 2, "deletions": 0, "truncated": False,
        "files": [{"path": "a.py", "status": "M", "additions": 2, "deletions": 0, "binary": False}],
        "diff": diff,
    }, ensure_ascii=False), encoding="utf-8")
    (run_dir / "events.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in [
        {"time": "2026-10-16T15:00:00+00:00", "event": "workspace_ready",
         "base_commit": "b" * 40, "worktree": str(run_dir / "worktrees")},
        {"time": "2026-10-16T15:01:00+00:00", "event": "verify_finished", "name": "unit", "exit_code": 0},
        {"time": "2026-10-16T15:02:00+00:00", "event": "review_finished", "attempt": 0,
         "verdict": "changes_requested", "findings": 1},
        {"time": "2026-10-16T15:03:00+00:00", "event": "rework_started", "attempt": 1, "findings": 1},
        {"time": "2026-10-16T15:04:00+00:00", "event": "tool_started", "phase": "implement", "name": "read_file"},
    ]) + "\n", encoding="utf-8")
    return run_dir


def render(tmp_path, state=None, **kwargs) -> str:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    run_dir = write_run(tmp_path / "runs" / "demo-run", **kwargs)
    return report.build(state or sample_state(root), run_dir, root)


def test_report_contains_the_full_evidence_chain(tmp_path):
    html = render(tmp_path)
    for section in ("需求来源", "关键指标", "阶段时间线", "实现报告", "候选改动", "独立复核", "审核与返工", "人工决定", "远端产物"):
        assert re.search(r'<h2(?: id="[^"]+")?>' + re.escape(section) + '</h2>', html), section
    assert "https://example.test/pull/2" in html
    assert "e3ac2677aa" in html
    assert "changes_requested" in html
    assert "缺边界用例" in html, "审核发现必须进入报告"
    assert "print(&#x27;hi&#x27;)" in html or "print('hi')" in html


def test_report_escapes_html_from_untrusted_fields(tmp_path):
    html = render(tmp_path)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_report_redacts_local_paths_and_credentials(tmp_path):
    root = tmp_path / "repo"
    state = sample_state(root)
    state["verified"][0]["output_tail"] += " " + str(root / "worktrees" / "demo") + " DS_API_KEY=sk-abcdef123456789012"
    html = render(tmp_path, state=state)
    assert str(root) not in html
    # 单元格内容会做 HTML 转义，因此脱敏标记以 &lt;…&gt; 形式出现。
    assert "<本机路径>" in html or "&lt;本机路径&gt;" in html
    assert "sk-abcdef123456789012" not in html
    assert "<凭证已脱敏>" in html or "&lt;凭证已脱敏&gt;" in html


def test_report_is_a_single_self_contained_document(tmp_path):
    html = render(tmp_path)
    assert html.startswith("<!DOCTYPE html>")
    assert "<style>" in html, "样式需内联，离线打开才有样子"
    assert "<script" not in html, "报告不应依赖脚本"
    assert "http://" not in html.replace("https://", ""), "不应引用外部资源"
    assert "src=" not in html and "href=\"http" not in html.replace("href='http", "")


def test_missing_artifacts_degrade_instead_of_failing(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    run_dir = tmp_path / "runs" / "empty"
    run_dir.mkdir(parents=True)
    state = {"run_id": "empty", "status": "failed", "stop_reason": "model_unavailable"}
    html = report.build(state, run_dir, root)
    assert "没有可展示的记录" in html
    assert "<h1>empty</h1>" in html
    assert "failed" in html


def test_write_creates_parent_directories(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    run_dir = write_run(tmp_path / "runs" / "demo-run")
    destination = tmp_path / "out" / "deep" / "report.html"
    path = report.write(sample_state(root), run_dir, root, destination)
    assert path.exists() and path.stat().st_size > 0
    assert "RepoPilot 运行报告" in path.read_text(encoding="utf-8")


def test_review_findings_fall_back_to_the_earlier_round(tmp_path):
    """最终审核通过时 review_report 没有发现，报告仍要展示上一轮的发现。"""

    html = render(tmp_path)
    assert "审核发现" in html
    assert "补一条空输入用例" in html


@pytest.mark.parametrize("status,expected", [
    ("published", "ok"),
    ("completed", "ok"),
    ("awaiting_approval", "warn"),
    ("failed", "bad"),
    ("limit_reached", "bad"),
    ("something_unknown", ""),
])
def test_status_colour_mapping(status, expected):
    assert report.status_class(status) == expected


def test_next_action_explains_the_human_gate(tmp_path):
    approved_state = {"status": "awaiting_approval", "run_id": "demo", "reviewed_commit": "a" * 40}
    text = report.next_action(approved_state)
    assert "等待人工审批" in text
    assert "approve demo" in text and "a" * 40 in text
    assert "publish" in report.next_action({"status": "approved", "run_id": "demo",
                                            "reviewed_commit": "a" * 40})
    assert "草稿 PR" in report.next_action({"status": "published"})
    assert report.next_action({"status": "completed"}) == ""


def test_meta_title_is_used_for_local_tasks_without_issue(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    run_dir = write_run(tmp_path / "runs" / "demo-run")
    (run_dir / "meta.json").write_text(json.dumps(
        {"title": "同步文档里的测试数量", "description": "只改两个文档"}, ensure_ascii=False), encoding="utf-8")
    state = {"run_id": "demo-run", "status": "awaiting_approval", "stop_reason": "awaiting_approval"}
    html = report.build(state, run_dir, root)
    assert "<h1>同步文档里的测试数量</h1>" in html
    assert "只改两个文档" in html


def test_run_id_is_escaped_in_the_next_action_block(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    run_dir = write_run(tmp_path / "runs" / "demo-run")
    state = {"run_id": "<img src=x onerror=alert(1)>", "status": "approved", "reviewed_commit": "b" * 40}
    html = report.build(state, run_dir, root)
    assert "<img" not in html, "未转义标签会真的变成元素"
    assert "&lt;img" in html


def test_delivery_overview_does_not_turn_completion_into_approval(tmp_path):
    from repopilot.presentation.layout import delivery_steps
    state = {'status': 'completed', 'phase': 'approval', 'report': {'status': 'blocked'}}
    assert all(kind == 'pending' for _, kind, _ in delivery_steps(state, {}))
    html = render(tmp_path, state=state)
    assert '报告流程结束' in html and '本次交付到草稿 PR，尚未启用自动合并' in html
    assert '不是实时监控' in html


def test_failed_checks_and_stale_approval_are_visible(tmp_path):
    from repopilot.presentation.layout import delivery_steps
    state = sample_state(tmp_path)
    state['verified'][0]['exit_code'] = 1
    state['reviewed_commit'] = 'new-head'
    steps = {name: (kind, detail) for name, kind, detail in delivery_steps(state, {})}
    assert steps['独立复核'][0] == 'failed'
    assert steps['人工审批'] == ('failed', '批准版本已变化')


def test_evidence_urls_cannot_execute_scripts(tmp_path):
    state = sample_state(tmp_path)
    state['issue']['url'] = 'javascript:alert(1)'
    state['pull_request']['url'] = 'data:text/html,<script>alert(1)</script>'
    html = render(tmp_path, state=state)
    assert "href='javascript:" not in html and "href='data:" not in html
    assert '<script>' not in html


def test_timeline_does_not_invent_findings_and_keeps_round_after_truncation(tmp_path):
    events = []
    for number in range(3):
        events.extend([{'event': 'review_started', 'commit': str(number)},
                       {'event': 'review_finished', 'verdict': 'changes_requested'}])
    rows = report.timeline_rows(events, tmp_path, limit=1)
    assert len(rows) == 1 and '第 3 轮' in rows[0][2]
    assert '0 条发现' not in rows[0][2]


def test_independent_check_error_cannot_be_rendered_as_passed(tmp_path):
    from repopilot.presentation.sections import checks
    html = checks([{'name': 'unit', 'exit_code': 0, 'error': 'transport_failed'}], tmp_path)
    assert 'check-result failed' in html and '未通过' in html
