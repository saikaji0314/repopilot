"""第 2 期步骤 2：Python AST 索引的行为测试。"""

import pytest

from repopilot.legacy.retrieval.index import build_index
from repopilot.legacy.retrieval.schema import SymbolKind


def test_build_index_finds_class_method_and_decorated_async_function(tmp_path):
    """索引限定名称、符号种类和包含装饰器的 1-based 行号。"""
    package = tmp_path / "flaskr"
    package.mkdir()
    (package / "service.py").write_text(
        "class Service:\n"
        "    @staticmethod\n"
        "    async def update():\n"
        "        return 1\n",
        encoding="utf-8",
    )

    index = build_index(tmp_path)

    assert index.files == ["flaskr/service.py"]
    assert [
        (s.path, s.qualified_name, s.kind, s.start_line, s.end_line)
        for s in index.symbols
    ] == [
        ("flaskr/service.py", "Service", SymbolKind.CLASS, 1, 4),
        ("flaskr/service.py", "Service.update", SymbolKind.ASYNC_FUNCTION, 2, 4),
    ]
    assert index.truncated is False


def test_build_index_keeps_same_name_in_different_files_and_stable_order(tmp_path):
    """同名函数靠路径区分；结果不随文件创建顺序变化。"""
    (tmp_path / "z.py").write_text("def update():\n    pass\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("def update():\n    pass\n", encoding="utf-8")

    index = build_index(tmp_path)

    assert index.files == ["a.py", "z.py"]
    assert [(s.path, s.qualified_name) for s in index.symbols] == [
        ("a.py", "update"),
        ("z.py", "update"),
    ]


def test_build_index_records_syntax_error_and_continues(tmp_path):
    """一个文件语法错误时仍索引其他文件，并留下可追溯的警告。"""
    (tmp_path / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    (tmp_path / "good.py").write_text("def working():\n    pass\n", encoding="utf-8")

    index = build_index(tmp_path)

    assert [(s.path, s.qualified_name) for s in index.symbols] == [
        ("good.py", "working")
    ]
    assert any("bad.py" in warning for warning in index.warnings)


def test_build_index_ignores_cache_and_git_directories(tmp_path):
    """忽略目录里的 Python 文件不进入索引。"""
    for dirname in (".git", "__pycache__"):
        ignored = tmp_path / dirname
        ignored.mkdir()
        (ignored / "secret.py").write_text("def hidden(): pass\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("def visible(): pass\n", encoding="utf-8")

    index = build_index(tmp_path)

    assert index.files == ["app.py"]
    assert [s.qualified_name for s in index.symbols] == ["visible"]


def test_build_index_does_not_execute_source_code(tmp_path):
    """AST 只解析源码，不运行仓库文件的顶层语句。"""
    (tmp_path / "app.py").write_text(
        "raise RuntimeError('must not run')\ndef safe():\n    pass\n",
        encoding="utf-8",
    )

    index = build_index(tmp_path)

    assert [s.qualified_name for s in index.symbols] == ["safe"]


def test_build_index_skips_file_link_outside_workspace(tmp_path):
    """工作区内的链接不能把外部 Python 文件带入索引。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("def external(): pass\n", encoding="utf-8")
    try:
        (workspace / "linked.py").symlink_to(outside)
    except OSError:
        pytest.skip("当前系统不允许创建文件符号链接")

    index = build_index(workspace)

    assert index.files == []
    assert index.symbols == []
