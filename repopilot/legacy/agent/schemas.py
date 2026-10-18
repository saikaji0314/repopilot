# 状态、轨迹事件和最终答案协议
from enum import StrEnum
from pydantic import BaseModel, ConfigDict, model_validator, Field
from typing import Any, Protocol
from repopilot.legacy.schemas import BaselineAnswer


class AgentStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    LIMIT_REACHED = "limit_reached"


class AgentRunStatus():
    ALLOWED_TRANSITIONS = {
        AgentStatus.CREATED: {AgentStatus.RUNNING},
        AgentStatus.RUNNING: {
            AgentStatus.COMPLETED,
            AgentStatus.FAILED,
            AgentStatus.LIMIT_REACHED,
        },
        AgentStatus.COMPLETED: set(),
        AgentStatus.FAILED: set(),
        AgentStatus.LIMIT_REACHED: set(),
    }

    def __init__(self) -> None:
        self._status = AgentStatus.CREATED

    @property
    def status(self) -> AgentStatus:
        return self._status

    def transition_to(self, status: AgentStatus):
        if status not in self.ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(f"Invalid status: {status}")
        self._status = status


class StopReason(StrEnum):
    FINAL_ANSWER = "final_answer"
    MODEL_ERROR = "model_error"
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_TOOL_ARGUMENTS = "invalid_tool_arguments"
    TOOL_ERROR = "tool_error"
    INVALID_FINAL_ANSWER = "invalid_final_answer"
    MAX_STEPS = "max_steps"
    MAX_TOOL_CALLS = "max_tool_calls"
    RUN_TIMEOUT = "run_timeout"
    TOKEN_BUDGET = "token_budget"
    COST_BUDGET = "cost_budget"


# -- 本地工具执行结果 --
class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    tool: str
    data: dict[str, Any] | None = None
    error: ToolError | None = None
    truncated: bool = False  # 搜索和文件读取使用

    @model_validator(mode="after")
    def validate_result(self):
        if self.ok and self.error:
            raise ValueError("成功不能有error")
        if not self.ok and not self.error:
            raise ValueError("失败必须有error")
        return self


# -- 一次模型调用的标准化结果，用于bind --
class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments_json: str = Field(min_length=1)


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


class ModelTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: Usage


# -- 模型适配器，BailianChatModel/FakeChatModel --
class ChatModel(Protocol):
    def complete(self, messages: list[dict], tools: list[dict], *, tool_choice: str | None = None) -> ModelTurn:
        ...


# -- 整个 Agent 运行结束后的结果 --
class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AgentStatus
    answer: BaselineAnswer | None = None
    stop_reason: StopReason
    model_steps: int = 0
    tool_calls: int = 0
    usage: Usage
    messages: list[dict]
