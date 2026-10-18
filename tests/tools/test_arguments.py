"""步骤 4：四个工具的 Pydantic 参数模型测试，不执行实际工具。"""

import pytest
from pydantic import ValidationError

from repopilot.legacy.tools.arguments import (
    ListFilesArguments,
    ReadFileArguments,
    RunTestArguments,
    SearchCodeArguments,
)


def test_list_files_arguments_accept_valid_depth():
    """测试合法的目录深度和路径会被保留。"""
    args = ListFilesArguments.model_validate_json('{"path":".","max_depth":2}')

    assert args.model_dump() == {"path": ".", "max_depth": 2}


@pytest.mark.parametrize("depth", [0, 5])
def test_list_files_arguments_reject_out_of_range_depth(depth):
    """测试 max_depth 只允许 1～4。"""
    with pytest.raises(ValidationError):
        ListFilesArguments.model_validate({"path": ".", "max_depth": depth})


def test_read_file_arguments_compare_start_and_end():
    """测试跨字段规则：结束行不能早于起始行。"""
    with pytest.raises(ValidationError):
        ReadFileArguments.model_validate(
            {"path": "app.py", "start_line": 10, "end_line": 3}
        )


@pytest.mark.parametrize("query", ["", "x" * 201])
def test_search_code_arguments_reject_empty_or_long_query(query):
    """测试搜索词长度在参数入口就受限。"""
    with pytest.raises(ValidationError):
        SearchCodeArguments.model_validate(
            {"query": query, "path": ".", "file_pattern": "*.py"}
        )


def test_run_test_arguments_reject_extra_fields():
    """测试模型不能偷偷附加工具未声明的参数。"""
    with pytest.raises(ValidationError):
        RunTestArguments.model_validate(
            {"test_target": "tests/test_app.py", "shell": True}
        )


def test_run_test_arguments_reject_empty_target():
    """测试空测试目标不能进入 pytest 工具。"""
    with pytest.raises(ValidationError):
        RunTestArguments.model_validate({"test_target": ""})
