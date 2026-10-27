"""文件正文、局部FTS更新、版本确认和历史追溯；不调用模型。"""

from contextlib import closing
import json

import pytest

from repopilot.execution.workspace import run_git
from repopilot.knowledge import store as store_module
from repopilot.knowledge.documents import KnowledgeFault, Material
from repopilot.knowledge.retrieval import manifest, related, search
from repopilot.knowledge.store import KnowledgeStore
from tests.runtime.test_review_loop import make_repo


@pytest.fixture
def knowledge(tmp_path):
    return make_repo(tmp_path), KnowledgeStore(tmp_path / "runtime")


def register(knowledge, identifier="rules", body="# Compatibility\n\n保留旧接口，空输入返回空结果。\n",
             *, confirm=True, **kwargs):
    repository, store = knowledge
    path = repository / "docs" / f"{identifier}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(body, encoding="utf-8")
    material = Material(id=identifier, title=identifier, kind=kwargs.pop("kind", "project"), **kwargs)
    record = store.register(repository, f"docs/{identifier}.md", material)
    if confirm:
        record = store.confirm(repository, identifier, version=record["version"], reason="fixture operator")
    return record


def indexed_rows(store):
    with closing(store.connect()) as db:
        return [tuple(row) for row in db.execute("SELECT * FROM material_chunks ORDER BY rowid")]


@pytest.mark.parametrize("kind", ["project", "task", "decision", "evidence"])
def test_four_types_start_as_proposals_and_confirm_specific_versions(knowledge, kind):
    repository, store = knowledge
    first = register(knowledge, confirm=False, kind=kind)
    assert first["status"] == "proposed"
    assert not search(store, repository, "旧接口")["entries"]
    assert search(store, repository, "旧接口", proposed=True)["entries"]
    confirmed = store.confirm(repository, first["id"], version=first["version"], reason="已核对")
    result = search(store, repository, "旧接口")["entries"][0]
    assert confirmed["confirmation"]["actor"] == "local-operator"
    assert result["version"] == first["version"] and "旧接口" in result["text"]


def test_update_only_parses_changed_document_and_keeps_other_rows(knowledge, monkeypatch):
    repository, store = knowledge
    first = register(knowledge, body="# Alpha\n\n旧约束。\n\n## Stable\n\nstable reference retained.\n")
    register(knowledge, "second", "# Other\n\nother unaffected material.\n")
    before = indexed_rows(store)
    calls, original = [], store_module.chunks
    def record(text):
        calls.append(text)
        return original(text)
    monkeypatch.setattr(store_module, "chunks", record)
    path = repository / "docs/rules.md"
    path.write_text(path.read_text(encoding="utf-8").replace("旧约束", "新约束"), encoding="utf-8")
    synced = store.sync(repository)
    assert synced == {"updated": ["rules"], "unchanged": ["second"]}
    assert len(calls) == 1
    after = indexed_rows(store)
    assert [row for row in before if row[2] == "second"] == [row for row in after if row[2] == "second"]
    stable = [row for row in before if row[4] == "Stable"]
    assert stable and stable == [row for row in after if row[4] == "Stable"]
    assert store.inspect(repository, "rules")["status"] == "proposed"
    assert "旧约束" in store.read(repository, "rules", version=first["version"])["text"]
    assert not search(store, repository, "新约束")["entries"]


def test_deletion_removes_current_search_but_keeps_history(knowledge):
    repository, store = knowledge
    first = register(knowledge)
    (repository / "docs/rules.md").unlink()
    assert store.sync(repository)["updated"] == ["rules"]
    assert store.inspect(repository, "rules")["status"] == "deleted"
    assert indexed_rows(store) == []
    assert not search(store, repository, "旧接口", proposed=True)["entries"]
    history = store.read(repository, "rules", version=first["version"])
    assert history["status"] == "historical" and "旧接口" in history["text"]


def test_removed_paragraph_does_not_remain_searchable(knowledge):
    repository, store = knowledge
    first = register(knowledge, body="# Rules\n\nretain paragraph.\n\nobsolete marker.\n")
    (repository / "docs/rules.md").write_text("# Rules\n\nretain paragraph.\n", encoding="utf-8")
    store.sync(repository)
    assert not search(store, repository, "obsolete", proposed=True)["entries"]
    assert search(store, repository, "retain", proposed=True)["entries"]
    assert "obsolete" in store.read(repository, "rules", version=first["version"])["text"]


def test_failed_batch_rolls_back_index_and_can_resume(knowledge, monkeypatch):
    repository, store = knowledge
    first = register(knowledge, body="# Old\n\nold one.\n")
    second = register(knowledge, "second", "# Old\n\nold two.\n")
    rows = indexed_rows(store)
    for name in ("rules", "second"):
        (repository / "docs" / f"{name}.md").write_text(f"# New\n\nchanged {name}.\n", encoding="utf-8")
    original = store._index
    def fail(db, record, parts):
        original(db, record, parts)
        if record["id"] == "second":
            raise RuntimeError("simulated index interruption")
    monkeypatch.setattr(store, "_index", fail)
    with pytest.raises(RuntimeError, match="interruption"):
        store.sync(repository)
    assert store.get(repository, "rules")["version"] == first["version"]
    assert store.get(repository, "second")["version"] == second["version"]
    assert indexed_rows(store) == rows
    monkeypatch.setattr(store, "_index", original)
    assert store.sync(repository)["updated"] == ["rules", "second"]
    assert search(store, repository, "changed", proposed=True)["entries"]


def test_old_confirmation_cannot_confirm_edited_text(knowledge):
    repository, store = knowledge
    first = register(knowledge)
    (repository / "docs/rules.md").write_text("# Changed\n\nnew requirements.\n", encoding="utf-8")
    with pytest.raises(KnowledgeFault, match="version_changed"):
        store.confirm(repository, "rules", version=first["version"], reason="old approval")
    assert store.get(repository, "rules")["status"] == "proposed"


def test_scopes_and_repository_identity_do_not_leak(knowledge, tmp_path):
    repository, store = knowledge
    register(knowledge, "private", "# Private\n\nprivate business.\n", scope="run-one")
    assert not search(store, repository, "private")["entries"]
    assert search(store, repository, "private", scope="run-one")["entries"]
    other = tmp_path / "other"
    other.mkdir()
    run_git(other, "init", "-q")
    assert store.records(other) == []
    assert not search(store, other, "private", scope="run-one")["entries"]


def test_relations_show_missing_and_deleted_targets(knowledge):
    repository, store = knowledge
    register(knowledge, "decision", relations={"implements": ["rule", "missing"]})
    register(knowledge, "rule")
    (repository / "docs/rule.md").unlink()
    store.sync(repository)
    assert related(store, repository, "decision") == [
        {"relation": "implements", "target": "rule", "status": "deleted"},
        {"relation": "implements", "target": "missing", "status": "missing"}]


def test_explicit_supersession_requires_confirmed_replacement(knowledge):
    repository, store = knowledge
    register(knowledge, required=True)
    replacement = register(knowledge, "next", confirm=False)
    with pytest.raises(KnowledgeFault, match="replacement_unconfirmed"):
        store.supersede(repository, "rules", replacement="next", reason="new policy")
    store.confirm(repository, "next", version=replacement["version"], reason="confirmed")
    store.supersede(repository, "rules", replacement="next", reason="new policy")
    assert [r["id"] for r in manifest(store, repository)["entries"]] == ["next"]


@pytest.mark.parametrize("path", ["../outside.md", ".env", ".git/config"])
def test_sensitive_or_escaping_source_is_denied(knowledge, path):
    repository, store = knowledge
    with pytest.raises(ValueError):
        store.register(repository, path, Material(id="bad", kind="project", title="bad"))
    assert store.records(repository) == []


def test_possible_secret_is_not_copied_or_indexed(knowledge):
    repository, store = knowledge
    source = repository / "secret.md"
    source.write_text("sk-" + "a" * 40, encoding="utf-8")
    with pytest.raises(KnowledgeFault, match="possible_secret"):
        store.register(repository, "secret.md", Material(id="secret", kind="project", title="secret"))
    assert store.records(repository) == []
    assert not list(store.root.glob("knowledge/**/versions/*.md"))


def test_corrupt_snapshot_is_not_used(knowledge):
    repository, store = knowledge
    first = register(knowledge)
    path = next(store.root.glob("knowledge/**/versions/*.md"))
    path.write_text("tampered history", encoding="utf-8")
    with pytest.raises(KnowledgeFault, match="snapshot_corrupt"):
        store.read(repository, "rules", version=first["version"])


def test_metadata_update_preserves_original_version_and_reindexes_title(knowledge):
    repository, store = knowledge
    first = register(knowledge, body="# Body\n\nunchanged text.\n")
    newer = store.register(repository, "docs/rules.md", Material(id="rules", kind="decision", title="uniqueheading"))
    assert newer["version"] != first["version"]
    assert search(store, repository, "uniqueheading", proposed=True)["entries"]
    assert store.read(repository, "rules", version=first["version"])["title"] == "rules"


def test_managed_machine_decision_stays_proposed_and_cannot_overwrite_confirmation(knowledge):
    repository, store = knowledge
    material = Material(id="choice", kind="decision", title="Use existing interface", scope="run-one")
    first = store.record(repository, "# Choice\n\nA concise reason.\n", material)
    assert first["status"] == "proposed"
    assert not manifest(store, repository, "run-one")["entries"]
    assert store.read(repository, "choice", version=first["version"])["text"].endswith("reason.")
    store.confirm(repository, "choice", version=first["version"], reason="reviewed")
    with pytest.raises(KnowledgeFault, match="immutable"):
        store.record(repository, "# Choice\n\nDifferent rule.\n", material)
    with closing(store.connect()) as db:
        row = db.execute("SELECT * FROM materials WHERE id='choice'").fetchone()
        assert "concise reason" not in json.dumps(dict(row))


def test_large_optional_line_is_explicitly_omitted_from_search(knowledge):
    repository, store = knowledge
    register(knowledge, body="oversized " * 1200)
    result = search(store, repository, "oversized")
    assert not result["entries"]
    assert result["omitted"] == [{"id": "rules", "reason": "knowledge_excerpt_too_large"}]


def test_source_relocation_keeps_historical_location(knowledge):
    repository, store = knowledge
    first = register(knowledge)
    target = repository / "docs/moved.md"
    target.write_text((repository / "docs/rules.md").read_text(encoding="utf-8"), encoding="utf-8")
    store.register(repository, "docs/moved.md", Material(id="rules", kind="project", title="rules"))
    assert store.read(repository, "rules", version=first["version"])["path"] == "docs/rules.md"


def test_requiredness_survives_supersession_and_replacement_changes(knowledge):
    repository, store = knowledge
    register(knowledge, required=True)
    register(knowledge, "next", required=False)
    store.supersede(repository, "rules", replacement="next", reason="replace with confirmed rule")
    entry = manifest(store, repository)["entries"][0]
    assert entry["id"] == "next" and entry["required"] is True
    assert entry["required_by"] == ["rules"]
    (repository / "docs/next.md").write_text("# Changed\n\nnew rule.\n", encoding="utf-8")
    with pytest.raises(KnowledgeFault, match="required_unconfirmed"):
        manifest(store, repository)
