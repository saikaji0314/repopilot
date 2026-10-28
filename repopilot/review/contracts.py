"""程序侧审核记录：合并后的发现、争议现场与复核结论。

模型只输出 [ReviewReport][repopilot.agents.reports.ReviewReport]／A/B／C 报告；
固定ID、严重程度合并、阻断判定和争议去重都由这里定义，不能由模型自己宣布通过。
"""

from typing import Literal

from pydantic import Field, model_validator

from repopilot.core.config import Contract

PERSPECTIVES = ("correctness", "context")
PERSPECTIVE_LABELS = {
    "correctness": "正确性与回归",
    "context": "需求与业务上下文",
}

# 合并后的发现状态：
# open=仍需处理；unverified=严重但证据不足，不能批准；disputed=等待争议复核或人判断；
# closed=复审确认已解决；invalid=确认不是问题或被争议复核排除。
FINDING_STATUSES = ("open", "unverified", "disputed", "closed", "invalid")


class FindingSource(Contract):
    """一条合并发现来自哪个视角；保留原始措辞，不因去重丢掉互补证据。"""

    perspective: Literal["correctness", "context"]
    index: int = Field(ge=0)
    severity: Literal["blocker", "major", "minor"]
    evidence_status: Literal["reproduced", "source_inference", "insufficient_information"]
    observation: str = Field(min_length=1, max_length=2000)
    suggestion: str = Field(min_length=1, max_length=2000)


class MergedFinding(Contract):
    """固定ID的审核发现；重审、返工反馈和争议复核都引用同一个ID。"""

    id: str = Field(pattern=r"^r[0-9]+-f[0-9]+$")
    key: str = Field(pattern=r"^[0-9a-f]{16}$")
    round: int = Field(ge=1)
    basis: Literal["code", "requirement", "material", "check", "missing"]
    path: str | None = Field(default=None, min_length=1, max_length=500)
    start_line: int | None = Field(default=None, ge=1)
    reference: str = Field(default="", max_length=1000)
    severity: Literal["blocker", "major", "minor"]
    evidence_status: Literal["reproduced", "source_inference", "insufficient_information"]
    requirement: str = Field(default="", max_length=1000)
    trigger: str = Field(default="", max_length=1000)
    impact: str = Field(default="", max_length=1000)
    observation: str = Field(min_length=1, max_length=2000)
    suggestion: str = Field(min_length=1, max_length=2000)
    sources: list[FindingSource] = Field(min_length=1, max_length=4)
    blocking: bool = True
    status: Literal["open", "unverified", "disputed", "closed", "invalid"] = "open"
    resolution: dict | None = None

    @model_validator(mode="after")
    def location_matches_basis(self):
        if self.basis == "code":
            if self.path is None or self.start_line is None:
                raise ValueError("a code finding requires path and start_line")
        elif not self.reference.strip():
            raise ValueError("a non-code finding requires a reference")
        return self


class DisputePosition(Contract):
    """争议发生时两位审核者对同一发现的处理意见。"""

    perspective: Literal["correctness", "context"]
    status: Literal["resolved", "still_open", "invalid", "unable_to_verify", "missing"]
    evidence_status: Literal["reproduced", "source_inference", "insufficient_information"]
    observation: str = Field(min_length=1, max_length=2000)


class DisputeRecord(Contract):
    """一条具体争议的完整现场：固定版本、双方意见、A/B/C证据与人判断。"""

    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^d-[0-9]+$")
    key: str = Field(pattern=r"^[0-9a-f]{16}$")
    round: int = Field(ge=1)
    commit: str = Field(min_length=8)
    basis_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    finding: dict
    positions: list[DisputePosition] = Field(min_length=2, max_length=2)
    a: dict | None = None
    b: dict | None = None
    c: dict | None = None
    outcome: Literal["pending", "confirmed", "refuted", "undetermined",
                     "reused_confirmed", "reused_refuted",
                     "human_confirmed", "human_refuted"] = "pending"
    reason: str = Field(default="", max_length=4000)
    created_at: str = ""
    decided_at: str | None = None
    human_request_id: str | None = None
