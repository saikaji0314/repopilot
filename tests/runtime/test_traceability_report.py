"""来源、固定历史正文和报告只读性；不调用模型或真实平台。"""

from contextlib import closing
import sqlite3

import pytest

from repopilot.knowledge.documents import Material
from repopilot.knowledge.store import KnowledgeStore
from repopilot.presentation.report import build
from repopilot.presentation.traceability import render
from tests.runtime.test_review_loop import setup


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/owner/repo/issues/1", ("github", "github.com", "owner/repo")),
    ("https://gitlab.example.com/group/project/-/issues/1", ("gitlab", "gitlab.example.com", "group/project")),
])
def test_platform_identity_disambiguates_same_issue_number(tmp_path, url, expected):
    html = render({"issue": {"url": url, "number": 1}}, tmp_path)
    for value in expected:
        assert value in html


def test_report_reads_exact_decision_version_without_migration_or_sync(tmp_path):
    task, _, root = setup(tmp_path)
    store = KnowledgeStore(root)
    material = Material(id="choice", kind="decision", title="选择", sources=["run:demo/phase:implement"])
    old = store.record(task.repository, "# 历史决定\n\n选择局部更新，减少无关索引重建。\n", material)
    store.record(task.repository, "# 新版决定\n\n当前版本不应替换历史正文。\n", material)
    # 报告读取旧schema时不得像可写Store初始化那样执行迁移。
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute("PRAGMA user_version=1")
    state = {"run_id": "demo", "task": task.model_dump(mode="json"), "decision_records": [
        {"id": "choice", "version": old["version"], "status": "proposed", "phase": "implement"}]}
    before = store.path.read_bytes()
    html = build(state, root / "runs/demo", root)
    assert "减少无关索引重建" in html and "当前版本不应替换历史正文" not in html
    assert old["version"] in html and "proposed" in html
    assert "run:demo/phase:implement" in html
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("failure", ["missing_db", "broken_db", "missing_body", "tampered_body", "missing_repo"])
def test_unavailable_history_never_substitutes_current_or_crashes(tmp_path, failure):
    task, _, root = setup(tmp_path)
    store = KnowledgeStore(root)
    record = store.record(task.repository, "# 固定正文\n\n原始理由\n",
                          Material(id="choice", kind="decision", title="选择"))
    state = {"run_id": "demo", "task": task.model_dump(mode="json"), "decision_records": [
        {"id": "choice", "version": record["version"], "status": "proposed"}]}
    with closing(store.connect()) as db:
        relative = db.execute("SELECT path FROM material_versions WHERE id='choice'").fetchone()[0]
    path = root / relative
    if failure == "missing_db":
        store.path.unlink()
    elif failure == "broken_db":
        store.path.write_bytes(b"not a sqlite database")
    elif failure == "missing_body":
        path.unlink()
    elif failure == "tampered_body":
        path.write_text("被篡改的内容", encoding="utf-8")
    else:
        state["task"]["repository"] = str(tmp_path / "gone")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    html = build(state, root / "runs/demo", root)
    assert "固定版本正文不可用" in html
    assert "被篡改的内容" not in html
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_request_provenance_escapes_content_and_rejects_executable_links(tmp_path):
    state = {"human_requests": [{"id": "<script>bad</script>", "kind": "clarification", "status": "pending",
        "version": "v1", "phase": "implement", "summary": "确认业务", "blocking": [{"subtask": "s2"}],
        "reply": {"action": "answer", "actor": "registered-owner", "channel": "issue",
                  "source": {"url": "javascript:alert(1)", "author": "<img src=x>"}}}]}
    html = render(state, tmp_path)
    assert "<script>" not in html and "<img" not in html
    assert "href='javascript:" not in html
    assert "当前等待事项" in html and "s2" in html and "v1" in html


@pytest.mark.parametrize("damage", ["invalid_utf8", "invalid_metadata_json"])
def test_one_damaged_decision_keeps_other_history_and_delivery_report_readonly(tmp_path, damage):
    task, _, root = setup(tmp_path)
    store = KnowledgeStore(root)
    old = store.record(task.repository, "# 历史\n\n损坏的旧理由\n",
                       Material(id="choice", kind="decision", title="选择"))
    store.record(task.repository, "# 当前\n\n不得回退到当前理由\n",
                 Material(id="choice", kind="decision", title="选择"))
    good = store.record(task.repository, "# 独立记录\n\n其他历史理由仍可回查\n",
                        Material(id="other", kind="decision", title="另一选择"))
    with closing(store.connect()) as db, db:
        path = root / db.execute("SELECT path FROM material_versions WHERE id='choice' AND version=?",
                                 (old["version"],)).fetchone()[0]
        if damage == "invalid_metadata_json":
            db.execute("UPDATE material_versions SET metadata='not json' WHERE id='choice' AND version=?",
                       (old["version"],))
    if damage == "invalid_utf8":
        path.write_bytes(b"\xff\xfe\x80")
    state = {"run_id": "demo", "status": "published", "task": task.model_dump(mode="json"),
        "decision_records": [{"id": item["id"], "version": item["version"], "status": "proposed"}
                             for item in (old, good)],
        "verified": [{"name": "independent", "exit_code": 0}],
        "approval": {"commit": "a" * 40, "approved": True, "reason": "固定版本批准证据"},
        "pull_request": {"url": "https://github.com/owner/repo/pull/2"}}
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    html = build(state, root / "runs/demo", root)
    assert "固定版本正文不可用" in html
    assert "其他历史理由仍可回查" in html
    assert "不得回退到当前理由" not in html
    assert "固定版本批准证据" in html and "independent" in html and "/pull/2" in html
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_cli_report_reads_decisions_from_explicit_runtime_not_source_root(tmp_path, monkeypatch):
    import repopilot.cli as cli
    from repopilot.storage.jobs import JobStore

    task, _, root = setup(tmp_path)
    store = KnowledgeStore(root)
    record = store.record(task.repository, "# 选择\n\nCLI也须读到固定理由。\n",
                          Material(id="choice", kind="decision", title="选择"))
    JobStore(root).create({"run_id": "demo", "task": task.model_dump(mode="json"),
        "decision_records": [{"id": "choice", "version": record["version"], "status": "proposed"}]})
    source_root = tmp_path / "source"
    source_root.mkdir()
    monkeypatch.setattr(cli, "PROJECT_ROOT", source_root)
    destination = tmp_path / "report.html"
    before = store.path.read_bytes()
    assert cli.main(["report", "demo", "--output", str(root), "--html", str(destination)]) == 0
    assert "CLI也须读到固定理由" in destination.read_text(encoding="utf-8")
    assert store.path.read_bytes() == before
    assert not (source_root / "knowledge.sqlite3").exists()
