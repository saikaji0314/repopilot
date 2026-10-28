"""实现与审核的结构化输出契约；不依赖模型循环或工具执行。"""

from typing import Literal

from pydantic import Field, model_validator

from repopilot.core.config import Contract


# 审核发现的证据状态：复现过、从固定源码推断、信息不足。三者不能互相顶替。
EVIDENCE_STATUSES = ("reproduced", "source_inference", "insufficient_information")
# 发现的依据：代码位置、确认验收／需求、资料原文、检查结果、缺失行为。
FINDING_BASES = ("code", "requirement", "material", "check", "missing")


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
    """审核发现：有依据、触发条件、影响和证据状态，不能为凑字段编造行号。"""

    basis: Literal["code", "requirement", "material", "check", "missing"] = "code"
    path: str | None = Field(default=None, min_length=1, max_length=500)
    start_line: int | None = Field(default=None, ge=1)
    reference: str = Field(default="", max_length=1000)
    severity: Literal["blocker", "major", "minor"]
    evidence_status: Literal["reproduced", "source_inference", "insufficient_information"] = "source_inference"
    requirement: str = Field(default="", max_length=1000)
    trigger: str = Field(default="", max_length=1000)
    impact: str = Field(default="", max_length=1000)
    observation: str = Field(min_length=1, max_length=2000)
    suggestion: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def location_matches_basis(self):
        if self.basis == "code":
            if self.path is None or self.start_line is None:
                raise ValueError("a code finding requires path and start_line")
            return self
        if not self.reference.strip():
            raise ValueError("a non-code finding requires a reference to the requirement, material or check")
        if self.path is not None or self.start_line is not None:
            raise ValueError("non-code findings must not fabricate a code location")
        return self


class FindingResponse(Contract):
    """返工复审对上一轮发现的处理结论；由程序按固定ID收集，不能新增发现。"""

    finding_id: str = Field(min_length=1, max_length=40)
    status: Literal["resolved", "still_open", "invalid", "unable_to_verify"]
    evidence_status: Literal["reproduced", "source_inference", "insufficient_information"] = "source_inference"
    observation: str = Field(min_length=1, max_length=2000)
    suggestion: str = Field(default="", max_length=2000)


class ReviewReport(Contract):
    """单个审核视角的结论；审核只读，因此只提问题与建议，不自己改代码。"""

    verdict: Literal["approved", "changes_requested", "unable_to_review"]
    summary: str = Field(min_length=1)
    findings: list[Finding] = Field(default_factory=list, max_length=20)
    responses: list[FindingResponse] = Field(default_factory=list, max_length=20)
    questions: list[str] = Field(default_factory=list, max_length=10)
    decisions: list[Decision] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def verdict_matches_findings(self):
        if self.verdict == "changes_requested" and not self.findings and not any(
                item.status == "still_open" for item in self.responses):
            raise ValueError("changes_requested requires at least one finding or a still_open response")
        if self.verdict == "approved" and any(item.severity in {"blocker", "major"} for item in self.findings):
            raise ValueError("approved cannot carry a blocker or major finding")
        if self.verdict == "approved" and any(item.status == "still_open" for item in self.responses):
            raise ValueError("approved cannot leave a previous finding still_open")
        identifiers = [item.finding_id for item in self.responses]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("each previous finding may be answered only once")
        return self


class DisputeReviewReport(Contract):
    """争议复核 A／B 的只读结论；没有证据时只能承认信息不足，不能给确定判断。"""

    judgement: Literal["issue_confirmed", "issue_refuted", "insufficient_evidence"]
    summary: str = Field(min_length=1, max_length=2000)
    evidence: list[FileEvidence | CheckEvidence] = Field(default_factory=list, max_length=10)
    materials: list[str] = Field(default_factory=list, max_length=10)
    reasoning: str = Field(default="", max_length=2000)
    decisions: list[Decision] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def firm_judgement_has_evidence(self):
        if self.judgement != "insufficient_evidence":
            if not self.evidence and not self.materials:
                raise ValueError("a firm dispute judgement requires file, check or material evidence")
            if not self.reasoning.strip():
                raise ValueError("a firm dispute judgement requires a short reason")
        return self


class DisputeAssessment(Contract):
    """C 对某一路证据的比较结论；side 对应 A 或 B 的报告。"""

    side: Literal["a", "b"]
    assessment: Literal["supported", "contradicted", "unclear"]
    observation: str = Field(min_length=1, max_length=2000)


class ArbitrationReport(Contract):
    """争议核对 C 的结论；可不改代码，必要时复现登记检查。"""

    outcome: Literal["confirmed", "refuted", "undetermined"]
    summary: str = Field(min_length=1, max_length=2000)
    comparison: list[DisputeAssessment] = Field(default_factory=list, max_length=8)
    verification: list[CheckEvidence] = Field(default_factory=list, max_length=8)
    reason: str = Field(default="", max_length=2000)
    decisions: list[Decision] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def firm_outcome_compares_evidence(self):
        if self.outcome != "undetermined":
            if not self.comparison:
                raise ValueError("a firm arbitration outcome requires comparing both sides")
            if not self.reason.strip():
                raise ValueError("a firm arbitration outcome requires a short reason")
        return self


class IntegrationReport(Contract):
    """整合冲突修复的结构化结果；不把“模型说已解决”当作冲突已消失。"""

    status: Literal["resolved", "needs_clarification", "blocked"]
    summary: str = Field(min_length=1, max_length=2000)
    decisions: list[Decision] = Field(default_factory=list, max_length=8)
    questions: list[str] = Field(default_factory=list, max_length=10)
