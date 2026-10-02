# 文件/符号/命中片段模型
from pydantic import BaseModel, ConfigDict
from enum import StrEnum


class SymbolKind(StrEnum):
    FUNCTION = "function"
    CLASS = "class"
    ASYNC_FUNCTION = "async_function"


class HitReason(StrEnum):
    EQUAL = "equal"
    PARTIAL = "partial"
    PATH = "path"


class Symbol(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    qualified_name: str
    kind: SymbolKind
    start_line: int
    end_line: int


class RepositoryIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbols: list[Symbol]
    files: list[str]
    warnings: list[str]
    truncated: bool


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    start_line: int | None = None
    end_line: int | None = None
    reason: HitReason
