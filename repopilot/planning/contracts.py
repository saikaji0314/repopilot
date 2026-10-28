"""计划报告与已确认计划的数据契约；只做结构校验，不调用模型或 Git。"""

from hashlib import sha256
import json
from typing import Literal

from pydantic import Field, field_validator, model_validator

from repopilot.agents.reports import Decision
from repopilot.core.config import Contract
from repopilot.storage.jobs import StateError

MAX_SUBTASKS = 8
MAX_LIST_ITEMS = 12
MAX_ITEM_CHARS = 1000


class PlanFault(StateError):
    """计划不满足可执行条件；不能被当成普通模型报告继续执行。"""


def digest(value) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def normalize(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def _clean_items(values, *, name):
    cleaned = []
    for value in values:
        text = " ".join(str(value).split())
        if not text:
            raise ValueError(f"{name} items must not be empty")
        if len(text) > MAX_ITEM_CHARS:
            raise ValueError(f"{name} items must be at most {MAX_ITEM_CHARS} characters")
        if text not in cleaned:
            cleaned.append(text)
    return cleaned


class BriefFields(Contract):
    """规划阶段整理出的任务说明；由程序保存版本，不由模型直接改写历史。"""

    goal: str = Field(min_length=1, max_length=4000)
    acceptance: list[str] = Field(min_length=1, max_length=MAX_LIST_ITEMS)
    assumptions: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    out_of_scope: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    unresolved: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    sources: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)

    @field_validator("goal")
    @classmethod
    def clean_goal(cls, value):
        text = " ".join(value.split())
        if not text:
            raise ValueError("goal must not be empty")
        return text

    @field_validator("acceptance", "assumptions", "out_of_scope", "unresolved", "sources")
    @classmethod
    def clean_lists(cls, value, info):
        return _clean_items(value, name=info.field_name)


class PlannedSubtask(Contract):
    """一个实现Agent可在充分额度内完成的子任务，含边界与验收。"""

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=2000)
    inputs: list[str] = Field(min_length=1, max_length=MAX_LIST_ITEMS)
    outputs: list[str] = Field(min_length=1, max_length=MAX_LIST_ITEMS)
    acceptance: list[str] = Field(min_length=1, max_length=MAX_LIST_ITEMS)
    depends_on: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    boundaries: list[str] = Field(min_length=1, max_length=MAX_LIST_ITEMS)
    interfaces: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    track: Literal["none", "issue"] = "none"
    track_reason: str = Field(default="", max_length=500)

    @field_validator("title", "goal", "track_reason")
    @classmethod
    def clean_text(cls, value):
        return " ".join(str(value).split())

    @field_validator("inputs", "outputs", "acceptance", "depends_on", "boundaries", "interfaces")
    @classmethod
    def clean_lists(cls, value, info):
        return _clean_items(value, name=info.field_name)

    @model_validator(mode="after")
    def tracking_reason(self):
        if self.track == "issue" and not self.track_reason:
            raise ValueError("track=issue requires a reason")
        if self.track == "none":
            self.track_reason = ""
        return self


class PlanReport(Contract):
    """规划角色的结构化输出；计划顺序由程序重新计算，不信模型自带顺序。"""

    status: Literal["ready", "needs_clarification"]
    summary: str = Field(min_length=1, max_length=2000)
    split: Literal["none", "sequential", "parallel"] = "none"
    split_reason: str = Field(default="", max_length=1000)
    brief: BriefFields | None = None
    subtasks: list[PlannedSubtask] = Field(default_factory=list, max_length=MAX_SUBTASKS)
    questions: list[str] = Field(default_factory=list, max_length=10)
    decisions: list[Decision] = Field(default_factory=list, max_length=8)

    @field_validator("summary", "split_reason")
    @classmethod
    def clean_summary(cls, value):
        return " ".join(str(value).split())

    @field_validator("questions")
    @classmethod
    def clean_questions(cls, value):
        return _clean_items(value, name="questions")

    @model_validator(mode="after")
    def ready_plan_shape(self):
        if self.status == "needs_clarification":
            if not self.questions:
                raise ValueError("needs_clarification requires at least one question")
            return self
        if self.brief is None:
            raise ValueError("a ready plan requires a task brief")
        if not self.split_reason:
            raise ValueError("a ready plan requires a split reason")
        if self.split == "none" and len(self.subtasks) != 1:
            raise ValueError("split=none requires exactly one subtask")
        if self.split == "sequential" and len(self.subtasks) < 2:
            raise ValueError("split=sequential requires at least two subtasks")
        if self.split == "parallel" and len(self.subtasks) < 2:
            raise ValueError("split=parallel requires at least two subtasks")
        if self.split == "parallel" and sum(not item.depends_on for item in self.subtasks) < 2:
            raise ValueError("split=parallel requires at least two independent subtasks")
        if self.split == "none" and any(item.track == "issue" for item in self.subtasks):
            raise ValueError("a non-split plan must not create sub-issue mappings")
        return self


class TaskPlan(Contract):
    """冻结后用于执行和审批追溯的计划；顺序字段必须是子任务ID的一个排列。"""

    schema_version: Literal[1] = 1
    version: str = Field(pattern=r"^plan-[0-9a-f]{12}$")
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    brief_revision: int = Field(ge=1)
    brief_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    split: Literal["none", "sequential", "parallel"]
    split_reason: str
    summary: str
    subtasks: list[PlannedSubtask] = Field(min_length=1, max_length=MAX_SUBTASKS)
    order: list[str] = Field(min_length=1, max_length=MAX_SUBTASKS)
    decisions: list[Decision] = Field(default_factory=list, max_length=8)
    created_at: str
    source: Literal["planner", "retry"] = "planner"

    @model_validator(mode="after")
    def order_matches_subtasks(self):
        if sorted(self.order) != sorted(item.id for item in self.subtasks):
            raise ValueError("order must contain every subtask exactly once")
        return self
