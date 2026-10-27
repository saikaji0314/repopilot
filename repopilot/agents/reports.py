"""实现与审核的结构化输出契约；不依赖模型循环或工具执行。"""

from typing import Literal

from pydantic import Field, model_validator

from repopilot.core.config import Contract


class FileEvidence(Contract):
    """指向工作区具体文件与行号的证据。"""

    kind: Literal["file"] = "file"
    path: str = Field(min_length=1)
    start_line: int = Field(ge=1)
    observation: str = Field(min_length=1)


class CheckEvidence(Contract):
    """指向某次已登记检查实际结果（退出码）的证据，不是文件位置。"""

    kind: Literal["check"] = "check"
    name: str = Field(min_length=1)
    exit_code: int
    observation: str = Field(min_length=1)


class Decision(Contract):
    """简短、可核对的决定理由；不是原始推理过程或自动确认的业务规则。"""
    summary: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)
    sources: list[str] = Field(default_factory=list, max_length=10)


class ImplementationReport(Contract):
    status: Literal["implemented", "needs_clarification", "blocked"]
    summary: str = Field(min_length=1)
    evidence: list[FileEvidence | CheckEvidence] = Field(default_factory=list, max_length=20)
    next_steps: list[str] = Field(default_factory=list, max_length=15)
    questions: list[str] = Field(default_factory=list, max_length=10)
    decisions: list[Decision] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def status_matches_evidence(self):
        if self.status == "implemented" and not self.evidence:
            raise ValueError("an implementation report requires evidence")
        if self.status == "implemented" and not any(item.kind == "check" for item in self.evidence):
            raise ValueError("an implementation report requires a check result as evidence")
        return self


class Finding(Contract):
    """审核发现；必须落在具体文件与行号上，并给出可执行的修改建议。"""

    path: str = Field(min_length=1)
    start_line: int = Field(ge=1)
    severity: Literal["blocker", "major", "minor"]
    observation: str = Field(min_length=1)
    suggestion: str = Field(min_length=1)


class ReviewReport(Contract):
    """审核结论；审核只读，因此只提问题与建议，不自己改代码。"""

    verdict: Literal["approved", "changes_requested", "unable_to_review"]
    summary: str = Field(min_length=1)
    findings: list[Finding] = Field(default_factory=list, max_length=20)
    questions: list[str] = Field(default_factory=list, max_length=10)
    decisions: list[Decision] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def verdict_matches_findings(self):
        if self.verdict == "changes_requested" and not self.findings:
            raise ValueError("changes_requested requires at least one finding")
        if self.verdict == "approved" and any(item.severity in {"blocker", "major"} for item in self.findings):
            raise ValueError("approved cannot carry a blocker or major finding")
        return self
