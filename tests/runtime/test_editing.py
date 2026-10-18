import pytest

from repopilot.core.config import Task
from repopilot.execution.repository import MAX_WRITE_CHARS, Repository, build_tools
from repopilot.execution.workspace import EditBudget


def tool(task, name, edits=None):
    tools = build_tools(task, lambda: 10, workspace=task.repository, edits=edits)
    return next(item for item in tools if item.name == name)


def task_with(root):
    return Task(id="edit", request="edit app.py", repository=root)


def test_write_creates_and_overwrites_utf8_text(tmp_path):
    write = tool(task_with(tmp_path), "write_file")
    created = write.invoke({"path": "pkg/main.py", "content": "value = 1\n"})
    assert created["ok"] and created["lines"] == 1
    assert (tmp_path / "pkg" / "main.py").read_text(encoding="utf-8") == "value = 1\n"
    overwritten = write.invoke({"path": "pkg/main.py", "content": "value = 2\n"})
    assert overwritten["ok"]
    assert (tmp_path / "pkg" / "main.py").read_text(encoding="utf-8") == "value = 2\n"


@pytest.mark.parametrize("path,error", [
    ("../outside.py", "path_denied"),
    (".env", "path_denied"),
    ("data/real.db", "path_denied"),
    ("C:secret", "path_denied"),
    ("node_modules/pkg/index.js", "path_denied"),
])
def test_write_rejects_paths_outside_the_allowed_area(tmp_path, path, error):
    result = tool(task_with(tmp_path), "write_file").invoke({"path": path, "content": "x"})
    assert result == {"ok": False, "error": error}
    assert not (tmp_path.parent / "outside.py").exists()


def test_write_rejects_oversized_and_binary_content(tmp_path):
    write = tool(task_with(tmp_path), "write_file")
    assert write.invoke({"path": "big.txt", "content": "x" * (MAX_WRITE_CHARS + 1)})["error"] == "content_too_large"
    assert write.invoke({"path": "bin.txt", "content": "a\0b"})["error"] == "binary_content"
    assert not (tmp_path / "big.txt").exists()


def test_replace_requires_a_unique_exact_match(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\nother = 1\n", encoding="utf-8")
    replace = tool(task_with(tmp_path), "replace_in_file")
    assert replace.invoke({"path": "app.py", "old": "value = 1", "new": "value = 2"})["line"] == 1
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "value = 2\nother = 1\n"
    (tmp_path / "app.py").write_text("old\nother\nold\nother\n", encoding="utf-8")
    assert replace.invoke({"path": "app.py", "old": "old\nother", "new": "x"})["error"] == "old_string_not_unique"
    assert replace.invoke({"path": "app.py", "old": "missing", "new": "x"})["error"] == "old_string_not_found"
    assert replace.invoke({"path": "app.py", "old": "", "new": "x"})["error"] == "empty_old_string"
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "old\nother\nold\nother\n"


def test_replace_matches_unix_text_inside_a_crlf_file(tmp_path):
    # 仓库里真实存在 CRLF 文件时，模型按 \n 给出的片段仍应能精确命中。
    (tmp_path / "crlf.py").write_bytes(b"value = 1\r\nother = 1\r\n")
    replace = tool(task_with(tmp_path), "replace_in_file")
    assert replace.invoke({"path": "crlf.py", "old": "value = 1\nother = 1",
                           "new": "value = 9\nother = 9"})["line"] == 1
    assert (tmp_path / "crlf.py").read_text(encoding="utf-8") == "value = 9\nother = 9\n"


def test_replace_cannot_escape_the_allowed_area(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    result = tool(task_with(tmp_path), "replace_in_file").invoke(
        {"path": ".git/config", "old": "a", "new": "b"})
    assert result == {"ok": False, "error": "path_denied"}


def test_editing_tools_are_absent_without_a_workspace(tmp_path):
    names = [item.name for item in build_tools(task_with(tmp_path), lambda: 10)]
    assert names == ["list_files", "read_file", "search_text", "run_registered_check"]


def test_edit_limit_stops_further_writes(tmp_path):
    edits = EditBudget(limit=1)
    write = tool(task_with(tmp_path), "write_file", edits=edits)
    assert write.invoke({"path": "a.txt", "content": "1\n"})["ok"]
    assert write.invoke({"path": "b.txt", "content": "2\n"})["error"] == "edit_limit_reached"
    assert not (tmp_path / "b.txt").exists()
    assert edits.snapshot()["edited_files"] == ["a.txt"]
