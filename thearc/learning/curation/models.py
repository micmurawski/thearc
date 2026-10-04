"""Assessment ledger inputs and addressed configuration operations."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from thearc.models.agent import Operation, ResourceTarget


class ReflectionEvidence(BaseModel):
    model_config = {"extra": "forbid"}
    session_id: str
    event_id: str


class ReflectionRating(str, Enum):
    HELPFUL = "helpful"
    NEUTRAL = "neutral"
    HARMFUL = "harmful"


class ReflectionItem(BaseModel):
    """An evidence-backed rating of one specific configuration resource/section."""

    model_config = {"extra": "forbid"}
    target: ResourceTarget
    rating: ReflectionRating
    reason: str = Field(min_length=1)
    evidence: list[ReflectionEvidence]
    limitations: list[str]


class AssessmentReport(BaseModel):
    """Validated observations and assessments supplied to the curation ledger."""

    id: str
    session_ids: list[str]
    summary: str = ""
    items: list[ReflectionItem] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    evidence_event_ids: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class Curation(BaseModel):
    """One proposed operation on a resource or Markdown section of a MetaAgent."""

    id: str
    target: ResourceTarget
    operation: Operation
    value: dict[str, Any] | str | None = None
    reflection_ids: list[str]
    evidence_event_ids: list[str] = Field(default_factory=list)
    rationale: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_value(self) -> Curation:
        if self.operation == Operation.REMOVE and self.value is not None:
            raise ValueError("REMOVE does not accept a value")
        if self.operation != Operation.REMOVE and self.value is None:
            raise ValueError("ADD and EDIT require a value")
        return self
