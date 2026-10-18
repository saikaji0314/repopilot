"""搜索范围、覆盖信息与默认兼容；验证真实文件行为，不调用模型。"""

import pytest

from repopilot.core.config import Task
import repopilot.execution.files as access
from repopilot.execution.files import Repository
from repopilot.execution.repository import build_tools


def write(root, path, content="needle\n"):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_directory_single_file_and_filename_pattern_have_root_relative_results(tmp_path):
    for path in ("src/main.ts", "src/nested/other.ts", "src/ignored.py", "docs/main.ts", "src/upper.TS"):
        write(tmp_path, path)
    repo = Repository(tmp_path)
    scoped = repo.search("needle", "src", "*.ts")
    assert [m["path"] for m in scoped["matches"]] == ["src/main.ts", "src/nested/other.ts"]
    assert scoped["scanned_files"] == 2 and scoped["visited_files"] == 4
    assert scoped["path"] == "src" and scoped["file_glob"] == "*.ts"
    assert scoped["complete"] and scoped["truncation_reason"] is None
    assert repo.listing("src", "*.ts")["files"] == [m["path"] for m in scoped["matches"]]
    assert repo.search("needle", "src/main.ts")["scanned_files"] == 1
    assert repo.listing("src/main.ts")["files"] == ["src/main.ts"]
    assert repo.search("needle", "src/main.ts", "*.py")["matches"] == []


def test_narrowing_bypasses_unrelated_file_and_result_caps(tmp_path):
    # 目标位于旧全仓搜索的 2000 文件上限之后。
    for index in range(2001):
        write(tmp_path, f"a{index:04}.md", "unrelated\n")
    write(tmp_path, "src/target.py")
    repo = Repository(tmp_path)
    broad = repo.search("needle")
    assert not broad["matches"] and not broad["complete"]
    assert broad["truncation_reason"] == "file_limit" and broad["scanned_files"] == 2000
    filtered = repo.search("needle", file_glob="*.py")
    assert filtered["matches"][0]["path"] == "src/target.py" and filtered["complete"]
    assert filtered["scanned_files"] == 1
    assert repo.search("needle", "src")["visited_files"] == 1
    assert repo.listing()["truncation_reason"] == "result_limit"
    assert repo.listing("src")["files"] == ["src/target.py"]


def test_match_limit_and_unreadable_files_are_not_complete_searches(tmp_path):
    write(tmp_path, "a.bin", "\0needle")
    write(tmp_path, "b.py", "needle\n" * 21)
    repo = Repository(tmp_path)
    broad = repo.search("needle")
    assert len(broad["matches"]) == 20 and broad["truncated"]
    assert broad["truncation_reason"] == "match_limit"
    assert broad["scanned_files"] == 2 and broad["skipped_files"] == 1
    skipped = repo.search("missing", "a.bin")
    assert not skipped["matches"] and not skipped["truncated"] and not skipped["complete"]
    assert skipped["skipped_files"] == 1
    empty = repo.search("missing", file_glob="*.rs")
    assert empty["complete"] and empty["scanned_files"] == 0


def test_filename_filter_still_has_bounded_enumeration(tmp_path, monkeypatch):
    for index in range(5):
        write(tmp_path, f"{index}.md")
    monkeypatch.setattr(access, "MAX_SEARCH_ENTRIES", 4)
    repo = Repository(tmp_path)
    for result in (repo.search("needle", file_glob="*.py"), repo.listing(file_glob="*.py")):
        assert result["truncated"] and result["truncation_reason"] == "entry_limit"
        assert result["visited_files"] == 4


@pytest.mark.parametrize("path", ["..", "../escape", ".git", ".local", ".env", "C:\\outside", "/etc"])
def test_scoping_does_not_relax_path_guards(tmp_path, path):
    repo = Repository(tmp_path)
    for action in (lambda: repo.search("needle", path), lambda: repo.listing(path)):
        with pytest.raises(ValueError, match="path_denied"):
            action()


@pytest.mark.parametrize("pattern", ["", "../*.py", "src/*.py", "**/*.py", "C:*.py", "a\0b", "x" * 121])
def test_invalid_filename_patterns_fail_before_search(tmp_path, pattern):
    with pytest.raises(ValueError, match="invalid_file_glob"):
        Repository(tmp_path).search("needle", file_glob=pattern)


def test_filters_cannot_expose_sensitive_files(tmp_path):
    for path in (".env", ".env.example", ".local/secret.py", "src/token.key", "src/app.py"):
        write(tmp_path, path)
    repo = Repository(tmp_path)
    assert repo.listing(file_glob=".env*")["files"] == [".env.example"]
    assert [m["path"] for m in repo.search("needle", file_glob="*.py")["matches"]] == ["src/app.py"]


def test_tools_accept_new_scope_and_keep_old_query_only_calls(tmp_path):
    write(tmp_path, "src/app.go")
    tools = {t.name: t for t in build_tools(Task(id="scope", request="inspect", repository=tmp_path), lambda: 10)}
    assert tools["list_files"].invoke({})["files"] == ["src/app.go"]
    assert tools["search_text"].invoke({"query": "needle"})["matches"]
    assert tools["search_text"].invoke({"query": "needle", "path": "src", "file_glob": "*.go"})["complete"]
    assert tools["list_files"].invoke({"path": "src", "file_glob": "*.go"})["files"] == ["src/app.go"]
    assert tools["search_text"].invoke({"query": "needle", "path": "missing"})["error"] == "path_missing"


def test_walk_errors_are_reported_instead_of_false_complete(tmp_path, monkeypatch):
    def denied_walk(*args, **kwargs):
        kwargs["onerror"](PermissionError("synthetic denied directory"))
        yield  # 保持 os.walk 的生成器调用约定。

    monkeypatch.setattr(access.os, "walk", denied_walk)
    tools = {t.name: t for t in build_tools(Task(id="scope", request="inspect", repository=tmp_path), lambda: 10)}
    assert tools["search_text"].invoke({"query": "needle"}) == {"ok": False, "error": "repository_io_error"}
