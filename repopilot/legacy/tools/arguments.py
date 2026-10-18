# 四个工具的 Pydantic 参数模型
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ListFilesArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    max_depth: int = Field(ge=1, le=4, description="目录遍历深度")


class ReadFileArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="相对工作区的文件路径")
    start_line: int = Field(ge=1, description="起始行号")
    end_line: int = Field(ge=1, description="结束行号")

    @model_validator(mode="after")
    def validate_line_range(self):
        if self.start_line > self.end_line:
            raise ValueError("start_line must be less than or equal to end_line")
        return self


class SearchCodeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200, description="区分大小写的字面子串搜索")
    path: str = Field(description="搜索起点，必须是相对工作区的目录，不能是文件路径。根目录填 .；例如查找 src/flask/sansio/scaffold.py 中的代码，可填 src/flask/sansio 并使用 *.py；读取指定文件使用 read_file")
    file_pattern: str = Field(description="仅支持扩展名模式，例如 *.py、*.html；不支持具体文件名 scaffold.py，也不支持任意 glob")


class SearchSymbolsArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200, description="函数名、类名或文件路径片段，例如 login、auth.py")
    limit: int = Field(ge=1, le=10, description="最多返回多少个检索命中")


class RunTestArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_target: str = Field(min_length=1, max_length=200, description="测试目录、文件或 pytest node ID，例如 tests/test_blog.py::test_update。仅填写目标，不包含 python、pytest 或任何命令行参数；程序会自动添加执行命令和固定参数")
