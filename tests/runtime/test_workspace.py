import subprocess

import pytest

from repopilot.runtime.workspace import EditBudget, Workspace, WorkspaceError, source_snapshot


def init_repo(root):
    root.mkdir(parents=True, exist_ok=True)

    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "synthetic@example.test")
    git("config", "user.name", "synthetic")
    git("config", "commit.gpgsign", "false")
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    git("add", "app.py")
    git("commit", "-q", "-m", "base")
    return root


def prepare(tmp_path):
    repo = init_repo(tmp_path / "repo")
    workspace = Workspace(repo, tmp_path / "runtime" / "worktrees").prepare()
    return repo, workspace


def test_prepare_creates_a_worktree_outside_the_source_repository(tmp_path):
    repo, workspace = prepare(tmp_path)
    worktree = workspace.repository()
    assert worktree.is_dir() and worktree != repo
    assert not worktree.is_relative_to(repo)
    assert workspace.base_commit
    assert (worktree / "app.py").read_text(encoding="utf-8") == "value = 1\n"


def test_worktree_requires_a_git_repository(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "app.py").write_text("value = 1\n", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="not a git work tree"):
        Workspace(plain, tmp_path / "worktrees").prepare()


def test_edits_stay_in_the_worktree_and_the_source_stays_unchanged(tmp_path):
    repo, workspace = prepare(tmp_path)
    before = source_snapshot(repo)
    worktree = workspace.repository()
    (worktree / "app.py").write_text("value = 2\n", encoding="utf-8")
    (worktree / "notes.md").write_text("new file\n", encoding="utf-8")
    changes = workspace.changes()
    assert changes["changed_files"] == 2
    assert changes["additions"] == 2 and changes["deletions"] == 1
    statuses = {item["path"]: item["status"] for item in changes["files"]}
    assert statuses == {"app.py": " M", "notes.md": "??"}
    assert source_snapshot(repo) == before
    assert (repo / "app.py").read_text(encoding="utf-8") == "value = 1\n"


def test_diff_reports_text_and_marks_binary_and_truncation(tmp_path):
    _, workspace = prepare(tmp_path)
    worktree = workspace.repository()
    (worktree / "app.py").write_text("value = 3\n", encoding="utf-8")
    (worktree / "blob.bin").write_bytes(b"\0\1\2")
    changes = workspace.changes()
    binary = {item["path"]: item["binary"] for item in changes["files"]}
    assert binary["blob.bin"] is True and binary["app.py"] is False
    result = workspace.diff()
    assert "-value = 1" in result["diff"] and "+value = 3" in result["diff"]
    assert "binary file added" in result["diff"] and not result["truncated"]
    assert workspace.diff(limit=10)["truncated"] is True


def test_edit_budget_limits_writes_and_records_touched_files():
    edits = EditBudget(limit=2)
    edits.take("app.py")
    edits.take("app.py")
    assert edits.snapshot() == {"edits": 2, "edit_limit": 2, "edited_files": ["app.py"]}
    with pytest.raises(ValueError, match="edit_limit_reached"):
        edits.take("app.py")
    assert edits.snapshot()["edits"] == 2
