"""步骤 2：固定字符串代码搜索的测试。"""

from pathlib import Path

import pytest

from repopilot.legacy.tools.search import search_code, search_symbols
from repopilot.legacy.retrieval.index import build_index


@pytest.mark.parametrize("line_count, expected_truncated", [(45, False), (180, True)])
def test_search_symbols_has_more_context_but_keeps_character_limit(
    tmp_path, line_count, expected_truncated,
):
    """测超过旧 1000 字符的函数可返回，超出新 4000 字符仍截断。"""
    source = "def investigate():\n" + "    evidence = 'useful-source-line'\n" * line_count
    (tmp_path / "app.py").write_text(source, encoding="utf-8")

    result = search_symbols(tmp_path, "investigate", 3, build_index(tmp_path))

    assert result.ok is True
    assert 1000 < len(result.data["context"]) <= 4000
    assert result.truncated is expected_truncated
    if not expected_truncated:
        assert f"{line_count + 1}:" in result.data["context"]


def test_search_code_finds_matches_in_nested_directories(tmp_path):
    """测试搜索会递归进入子目录，并返回文件和 1-based 行号。"""
    package = tmp_path / "flaskr"
    package.mkdir()
    (package / "blog.py").write_text(
        "def index():\n    return get_post(1)\n",
        encoding="utf-8",
    )

    result = search_code(tmp_path, Path("."), "get_post", "*.py")

    assert result.ok is True
    assert result.data["matches"] == [
        {
            "path": "flaskr/blog.py",
            "line": 2,
            "text": "    return get_post(1)",
        }
    ]


def test_search_code_rejects_empty_query(tmp_path):
    """测试空字符串不会被当作匹配所有行的搜索条件。"""
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")

    result = search_code(tmp_path, Path("."), "", "*.py")

    assert result.ok is False
    assert result.error.code == "empty_query"


def test_search_code_marks_small_complete_result_as_not_truncated(tmp_path):
    """测试结果未达到上限时 truncated 为 False。"""
    (tmp_path / "app.py").write_text("needle = True\n", encoding="utf-8")

    result = search_code(tmp_path, Path("."), "needle", "*.py")

    assert result.ok is True
    assert len(result.data["matches"]) == 1
    assert result.truncated is False


def test_search_code_rejects_database_pattern(tmp_path):
    """测试简单扩展名模式仍需限制在允许的文本类型内。"""
    (tmp_path / "data.db").write_text("needle=secret\n", encoding="utf-8")

    result = search_code(tmp_path, Path("."), "needle", "*.db")

    assert result.ok is False
    assert result.error.code == "invalid_pattern"


def test_search_code_does_not_enter_ignored_directories(tmp_path):
    """测试搜索不会把缓存或 Git 元数据当作仓库源码证据。"""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hidden.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("nothing here\n", encoding="utf-8")

    result = search_code(tmp_path, Path("."), "needle", "*.py")

    assert result.ok is True
    assert result.data["matches"] == []


def test_search_code_does_not_follow_symlink_to_file_outside_workspace(tmp_path):
    """测试工作区内的符号链接不能让搜索读取工作区外文件。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("needle=secret\n", encoding="utf-8")
    try:
        (workspace / "linked.py").symlink_to(outside)
    except OSError:
        pytest.skip("当前 Windows 环境不允许创建符号链接")

    result = search_code(workspace, Path("."), "needle", "*.py")

    assert result.ok is True
    assert result.data["matches"] == []


def test_search_code_treats_regex_characters_as_literal_text(tmp_path):
    """测试 `.*` 只是普通字符，不会匹配任意内容。"""
    (tmp_path / "app.py").write_text("aXb\na.*b\n", encoding="utf-8")

    result = search_code(tmp_path, Path("."), "a.*b", "*.py")

    assert result.ok is True
    assert result.data["matches"] == [
        {"path": "app.py", "line": 2, "text": "a.*b"}
    ]


def test_search_code_is_case_sensitive(tmp_path):
    """测试大小写不同的查询不会命中同一行。"""
    (tmp_path / "app.py").write_text("title = 'hello'\n", encoding="utf-8")

    result = search_code(tmp_path, Path("."), "Title", "*.py")

    assert result.ok is True
    assert result.data["matches"] == []
    assert result.truncated is False


def test_search_code_reports_missing_directory(tmp_path):
    """测试目录不存在时返回错误，不伪装成完整搜索但没有命中。"""
    result = search_code(tmp_path, Path("missing"), "needle", "*.py")

    assert result.ok is False
    assert result.error.code == "path_not_found"


def test_search_code_rejects_file_as_search_directory(tmp_path):
    """测试 path 必须是目录，不能传入单个文件。"""
    (tmp_path / "app.py").write_text("needle\n", encoding="utf-8")

    result = search_code(tmp_path, Path("app.py"), "needle", "*.py")

    assert result.ok is False
    assert result.error.code == "not_a_directory"
    assert "父目录" in result.error.message
    assert "read_file" in result.error.message


def test_search_code_rejects_direct_access_to_ignored_directory(tmp_path):
    """测试直接指定忽略目录时也不能读取其中的内容。"""
    ignored = tmp_path / ".git"
    ignored.mkdir()
    (ignored / "hidden.py").write_text("needle\n", encoding="utf-8")

    result = search_code(tmp_path, Path(".git"), "needle", "*.py")

    assert result.ok is False
    assert result.data.get("matches", []) == []
