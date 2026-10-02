# 工具协议、统一结果和注册表
import json
from pathlib import Path
from abc import ABC, abstractmethod
from pydantic import BaseModel

from repopilot.agent.schemas import ToolResult, ToolError, StopReason
from repopilot.tools.filesystem import list_files, read_file
from repopilot.tools.search import search_code, search_symbols
from repopilot.tools.testing import run_test
from repopilot.tools.arguments import ListFilesArguments, ReadFileArguments, SearchCodeArguments, SearchSymbolsArguments, RunTestArguments
from repopilot.retrieval.index import RepositoryIndex


class Tool(ABC):
    name: str
    description: str
    args_model: BaseModel

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
                "strict": True,
            },
        }

    @abstractmethod
    def execute(self, workspace: Path, arguments: dict) -> ToolResult:
        ...


class ListFilesTool(Tool):
    name = "list_files"
    description = "列出工作区指定目录中的文件和子文件"
    args_model = ListFilesArguments

    def execute(self, workspace: Path, arguments: dict) -> ToolResult:
        return list_files(
            workspace, 
            relative_path=Path(arguments["path"]), 
            max_depth=arguments["max_depth"]
        )


class ReadFileTool(Tool):
    name = "read_file"
    description = "读取工作区指定文件的内容"
    args_model = ReadFileArguments

    def execute(self, workspace: Path, arguments: dict) -> ToolResult:
        return read_file(
            workspace, 
            relative_path=Path(arguments["path"]), 
            start=arguments["start_line"], 
            end=arguments["end_line"]
        )


class SearchCodeTool(Tool):
    name = "search_code"
    description = (
        "在工作区指定目录及其子目录中搜索代码正文，使用区分大小写的字面子串匹配。"
        "定位 Python 函数或类的定义且已知名称时，优先使用 search_symbols；"
        "查找变量、属性、配置键、调用位置或任意正文时使用本工具。"
        "path 必须是目录，不能是文件。读取已知文件范围使用 read_file。"
    )
    args_model = SearchCodeArguments

    def execute(self, workspace: Path, arguments: dict) -> ToolResult:
        return search_code(
            workspace, 
            query=arguments["query"], 
            relative_path=Path(arguments["path"]), 
            file_pattern=arguments["file_pattern"]
        )


class SearchSymbolsTool(Tool):
    name = "search_symbols"
    description = (
        "按 Python 函数名、类名或文件路径片段查找代码；"
        "定位函数或类的定义且已知名称时，优先使用本工具，query 只填名称（如 route），不填 def route。"
        "区分大小写，不支持正则或自然语言语义搜索。"
        "返回带路径和行号的匹配代码片段；变量、属性、配置键及调用位置使用 search_code。"
        "若片段截断或需要更多上下文，用 read_file 读取已知文件范围。"
    )
    args_model = SearchSymbolsArguments

    def __init__(self, index: RepositoryIndex):
        self.index = index

    def execute(self, workspace: Path, arguments: dict) -> ToolResult:
        return search_symbols(
            workspace, 
            query=arguments["query"], 
            limit=arguments["limit"], 
            index=self.index
        )


class RunTestTool(Tool):
    name = "run_test"
    description = "运行工作区指定测试"
    args_model = RunTestArguments

    def __init__(self, python_executable: Path | None = None):
        self.python_executable = python_executable

    def execute(self, workspace: Path, arguments: dict) -> ToolResult:
        return run_test(
            workspace, 
            test_target=arguments["test_target"],
            python_executable=self.python_executable,
        )


class ToolRegistry:
    def __init__(self, workspace: Path, tools: list[Tool]):
        self.workspace = workspace
        self.tools = tools
        # 检查同名工具
        if len(set([tool.name for tool in self.tools])) != len(self.tools):
            raise ValueError("Duplicate tool names are not allowed")
        # 发给模型的json schema
        self.schema = [tool.schema for tool in self.tools]

    def execute(self, name: str, arguments: str):
        # 校验工具是否存在
        tool = next((tool for tool in self.tools if tool.name == name), None)
        if tool is None:
            return ToolResult(ok=False, tool=name, data=None, error=ToolError(code=StopReason.UNKNOWN_TOOL, message=f"tool {name} not found"), truncated=False)
        # 校验参数
        try:
            args = tool.args_model.model_validate_json(arguments)
        except ValueError:
            return ToolResult(ok=False, tool=name, data=None, error=ToolError(code=StopReason.INVALID_TOOL_ARGUMENTS, message="arguments must match tool schema"), truncated=False)
        
        # 执行
        try:
            return tool.execute(self.workspace, args.model_dump())
        except Exception as e:
            return ToolResult(ok=False, tool=name, data=None, error=ToolError(code=StopReason.TOOL_ERROR, message=f"tool {name} execution failed"), truncated=False)
