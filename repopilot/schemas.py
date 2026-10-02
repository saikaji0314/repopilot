"""LLM 基线的结构化输出协议。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    observation: str = Field(min_length=1)


class BaselineAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["diagnosed", "insufficient_evidence"]
    summary: str = Field(min_length=1)
    root_cause: str = Field(min_length=1)
    evidence: list[EvidenceItem]
    proposed_fix: list[str]
    verification: list[str]
    risks: list[str]
