import json
from pathlib import Path
import shutil

import pytest

from repopilot.runtime.config import Check, Task, load_task
from repopilot.runtime.repository import Repository, build_tools, run_check


@pytest.mark.parametrize("name", ["main.py", "main.mjs", "main.go", "main.rs", "Makefile"])
def test_utf8_source_access_is_not_bound_to_python_suffixes(tmp_path, name):
    (tmp_path / name).write_text("hello 世界", encoding="utf-8")
    assert "hello 世界" in Repository(tmp_path).read(name, 1, 1)["content"]


@pytest.mark.parametrize("path", ["../oracle.yaml", ".env", ".env.local", ".local/notes.md",
                                   ".git/config", "data/real.db", "C:secret", "file:stream"])
def test_paths_and_sensitive_files_are_denied(tmp_path, path):
    with pytest.raises(ValueError, match="path_denied"):
        Repository(tmp_path).resolve(path)


def test_links_are_not_followed_or_listed(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("host cannot create symbolic links")
    repo = Repository(tmp_path)
    assert "link.txt" not in list(repo.files())
    with pytest.raises(ValueError, match="link_denied"):
        repo.text("link.txt")


def test_binary_and_large_files_are_rejected_and_search_reports_truncation(tmp_path):
    (tmp_path / "image.bin").write_bytes(b"a\0b")
    (tmp_path / "large.txt").write_bytes(b"x" * 256_001)
    (tmp_path / "lines.js").write_text("match\n" * 25, encoding="utf-8")
    repo = Repository(tmp_path)
    with pytest.raises(ValueError, match="binary_file"):
        repo.text("image.bin")
    with pytest.raises(ValueError, match="file_too_large"):
        repo.text("large.txt")
    result = repo.search("match")
    assert result["truncated"] and len(result["matches"]) == 20
    assert result["skipped_files"] == 2


def task_with(tmp_path, code, **kwargs):
    return Task(id="check", request="inspect", repository=tmp_path,
                checks={"unit": Check(argv=["{python}", "-c", code], **kwargs)})


def test_registered_check_retains_exit_code_and_strips_parent_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("DS_API_KEY", "synthetic-private")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-legacy")
    code = "import os,sys; print('DS_API_KEY' in os.environ, 'DASHSCOPE_API_KEY' in os.environ);sys.exit(7)"
    result = run_check(task_with(tmp_path, code), "unit", 10)
    assert result["exit_code"] == 7
    assert "False False" in result["output"]
    assert not result["timed_out"]


def test_check_timeout_and_output_truncation(tmp_path):
    result = run_check(task_with(tmp_path, "import time;time.sleep(30)", timeout_seconds=0.2), "unit", 10)
    assert result["timed_out"] and result["exit_code"] != 0
    result = run_check(task_with(tmp_path, "print('x' * 9000)"), "unit", 10)
    assert result["truncated"] and len(result["output"]) <= 8000


def test_named_tool_rejects_unknown_and_repeated_checks(tmp_path):
    task = task_with(tmp_path, "print('checked')")
    tool = next(t for t in build_tools(task, lambda: 10) if t.name == "run_registered_check")
    assert tool.invoke({"name": "unit && other"})["error"] == "unknown_check"
    assert tool.invoke({"name": "unit"})["exit_code"] == 0
    assert tool.invoke({"name": "unit"})["error"] == "check_already_ran"
    escaped = task_with(tmp_path, "print('must not run')", cwd="..")
    with pytest.raises(ValueError):
        run_check(escaped, "unit", 10)


def test_javascript_example_executes_with_node():
    if not shutil.which("node"):
        pytest.skip("Node.js is not installed")
    root = Path(__file__).resolve().parents[2]
    task = load_task(root / "examples/runtime/javascript-inspect.yaml")
    result = run_check(task, "unit", 10)
    assert result["exit_code"] == 0, json.dumps(result)
    assert not result["timed_out"]
