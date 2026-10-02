"""步骤 2：目录列举和文件片段读取的测试。"""

from pathlib import Path

from repopilot.tools.filesystem import list_files, read_file


def test_list_files_returns_sorted_files_and_directories(tmp_path):
    """测试目录结果有类型、顺序稳定，并忽略缓存目录和数据库文件。"""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "module.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (tmp_path / "data.db").write_text("secret\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "hidden.py").write_text("hidden\n", encoding="utf-8")

    result = list_files(tmp_path, Path("."), max_depth=2)

    assert result.ok is True
    assert result.data["entries"] == [
        {"path": "app.py", "type": "file"},
        {"path": "pkg", "type": "directory"},
        {"path": "pkg/module.py", "type": "file"},
    ]
    assert result.truncated is False


def test_list_files_rejects_invalid_depth(tmp_path):
    """测试目录深度只能在 1 到 4 之间。"""
    result = list_files(tmp_path, Path("."), max_depth=5)

    assert result.ok is False
    assert result.error.code == "invalid_depth"


def test_list_files_sorts_entries_independently_of_creation_order(tmp_path):
    """测试结果顺序不依赖操作系统的目录枚举顺序。"""
    (tmp_path / "z.py").write_text("z\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("a\n", encoding="utf-8")

    result = list_files(tmp_path, Path("."), max_depth=1)

    assert result.ok is True
    assert [entry["path"] for entry in result.data["entries"]] == ["a.py", "z.py"]


def test_read_file_returns_requested_lines_with_one_based_numbers(tmp_path):
    """测试 start 和 end 指定的两端行都包含在读取结果中。"""
    source = tmp_path / "example.py"
    source.write_text("first\nsecond\nthird\nfourth\n", encoding="utf-8")

    result = read_file(tmp_path, Path("example.py"), start=2, end=3)

    assert result.ok is True
    assert result.data["content"] == "2: second\n3: third"
    assert result.truncated is False


def test_read_file_rejects_database_files(tmp_path):
    """测试数据库等非源码文件不能通过 read_file 读取。"""
    (tmp_path / "data.db").write_text("password=secret\n", encoding="utf-8")

    result = read_file(tmp_path, Path("data.db"), start=1, end=1)

    assert result.ok is False
    assert result.error.code == "unsupported_file_type"


def test_read_file_rejects_directory_with_source_suffix(tmp_path):
    """测试名为 .py 的目录也不能作为源码文件读取。"""
    (tmp_path / "folder.py").mkdir()

    result = read_file(tmp_path, Path("folder.py"), start=1, end=1)

    assert result.ok is False
    assert result.error.code == "not_a_file"
