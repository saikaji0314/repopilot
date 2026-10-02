"""步骤 4：工具注册表的契约测试；不调用模型或真实 pytest。"""

from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, StrictStr

from repopilot.agent.schemas import ToolResult
from repopilot.tools.base import ReadFileTool, Tool, ToolRegistry


class EchoArgs(BaseModel):
    """测试专用参数模型：Schema 和运行时校验共用一份定义。"""

    model_config = ConfigDict(extra="forbid")
    text: StrictStr


class EchoTool(Tool):
    """最小假工具：方便单独测试注册表，不依赖文件系统工具的实现。"""

    name = "echo"
    description = "返回输入文本"
    args_model = EchoArgs

    def __init__(self):
        self.calls = []

    def execute(self, workspace: Path, arguments: dict) -> ToolResult:
        self.calls.append((workspace, arguments))
        return ToolResult(ok=True, tool=self.name, data={"text": arguments["text"]})


class BrokenTool(EchoTool):
    """模拟工具实现意外抛异常。"""

    def execute(self, workspace: Path, arguments: dict) -> ToolResult:
        raise RuntimeError("内部异常不应直接暴露给模型")


def test_registry_exports_complete_function_schema_without_workspace_root(tmp_path):
    """测试发给模型的是完整 function schema，且没有内部工作区参数。"""
    tool = EchoTool()
    registry = ToolRegistry(tmp_path, [tool])

    assert registry.schema == [tool.schema]
    assert registry.schema[0]["function"]["parameters"] == EchoArgs.model_json_schema()
    assert "workspace" not in str(registry.schema)


def test_registry_rejects_duplicate_tool_names(tmp_path):
    """测试同名工具在启动时就报错，避免运行时随机选中一个。"""
    with pytest.raises(ValueError):
        ToolRegistry(tmp_path, [EchoTool(), EchoTool()])


def test_registry_executes_valid_json_arguments(tmp_path):
    """测试 JSON 参数经注册表传给工具，并固定使用本次工作区。"""
    tool = EchoTool()
    registry = ToolRegistry(tmp_path, [tool])

    result = registry.execute("echo", '{"text":"hello"}')

    assert result.ok is True
    assert result.data == {"text": "hello"}
    assert tool.calls == [(tmp_path, {"text": "hello"})]


def test_registry_rejects_unknown_tool(tmp_path):
    """测试模型请求未注册工具时只得到结构化失败。"""
    result = ToolRegistry(tmp_path, [EchoTool()]).execute("delete_files", "{}")

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "unknown_tool"


@pytest.mark.parametrize(
    "arguments",
    [
        '{"text":',              # JSON 格式错误
        "[]",                    # 顶层不是对象
        "{}",                    # 缺少必填参数
        '{"text":42}',           # 参数类型错误
        '{"text":"hi","x":1}',  # 不允许额外参数
    ],
)
def test_registry_rejects_invalid_arguments_before_tool_execution(tmp_path, arguments):
    """测试坏参数不会进入工具实现，避免 KeyError 或越权参数。"""
    tool = EchoTool()
    result = ToolRegistry(tmp_path, [tool]).execute("echo", arguments)

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "invalid_tool_arguments"
    assert tool.calls == []


def test_registry_converts_tool_exception_to_result(tmp_path):
    """测试工具内部异常被统一转换，Agent Loop 仍可记录失败原因。"""
    result = ToolRegistry(tmp_path, [BrokenTool()]).execute("echo", '{"text":"hi"}')

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "tool_error"
    assert "内部异常不应直接暴露" not in result.error.message


def test_read_file_works_through_registry_with_json_string_path(tmp_path):
    """集成测试：模型的字符串路径要能传过注册表和 read_file 适配器。"""
    (tmp_path / "example.py").write_text("first\nsecond\n", encoding="utf-8")
    registry = ToolRegistry(tmp_path, [ReadFileTool()])

    result = registry.execute(
        "read_file", '{"path":"example.py","start_line":1,"end_line":2}'
    )

    assert result.ok is True
    assert result.data["content"] == "1: first\n2: second"
