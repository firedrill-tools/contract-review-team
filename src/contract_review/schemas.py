"""Strict native structured role data; interpretation remains a proposal."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .strict import MAX_INTEGER

Rating = Literal["low", "medium", "high", "unknown"]


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class SourceSpan(StrictModel):
    start: int = Field(ge=0, le=MAX_INTEGER)
    end: int = Field(ge=1, le=MAX_INTEGER)
    quote: str = Field(min_length=1, max_length=4096)


class UnitProposal(StrictModel):
    unit_id: str = Field(min_length=1, max_length=64)
    evidence: SourceSpan
    party: SourceSpan | None
    action: SourceSpan
    deadline: SourceSpan | None
    uncertainty: str = Field(max_length=1000)


class ExtractedUnits(StrictModel):
    units: list[UnitProposal] = Field(max_length=128)


class UnitAssessment(StrictModel):
    unit_id: str = Field(min_length=1, max_length=64)
    evidence: SourceSpan
    severity: Rating
    rationale: str = Field(min_length=8, max_length=2000)
    uncertainty: str = Field(max_length=1000)


class CrossReference(StrictModel):
    unit_ids: list[str] = Field(min_length=2, max_length=8)
    rationale: str = Field(min_length=8, max_length=2000)


class IndependentRiskReview(StrictModel):
    overall: Rating
    assessments: list[UnitAssessment] = Field(max_length=128)
    cross_references: list[CrossReference] = Field(max_length=64)


class ReviewSummary(StrictModel):
    narrative: str = Field(min_length=20, max_length=12000)
    unit_ids: list[str] = Field(min_length=1, max_length=128)
    notice_candidate_ids: list[str] = Field(max_length=128)
    rating_difference_ids: list[str] = Field(max_length=129)


SCHEMAS = {
    "extraction": ExtractedUnits,
    "risk_anthropic": IndependentRiskReview,
    "risk_openai": IndependentRiskReview,
    "summary": ReviewSummary,
}
