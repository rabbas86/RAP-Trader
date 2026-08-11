"""Strict contracts for the offline, research-only Investment Committee."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.models.analyst import AnalysisTrace, DataFreshness
from app.domain.models.market_data import UtcDatetime, _require_aware_utc
from app.domain.models.risk import RiskDecisionType


class _CommitteeModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)


class _ResearchOnly(_CommitteeModel):
    research_only: Literal[True] = True
    suitable_for_live_trading: Literal[False] = False
    decision_ready: Literal[False] = False


class CommitteeMemberRole(StrEnum):
    TECHNICAL_ANALYST = "technical_analyst"
    FUNDAMENTAL_ANALYST = "fundamental_analyst"
    MACRO_ECONOMIST = "macro_economist"
    NEWS_ANALYST = "news_analyst"
    PORTFOLIO_MANAGER = "portfolio_manager"
    RISK_OFFICER = "risk_officer"


class CommitteeMemberView(_CommitteeModel):
    role: CommitteeMemberRole
    source_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    direction_or_status: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    freshness: DataFreshness | None = None
    summary: str = Field(min_length=1)
    warnings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    available_at: UtcDatetime
    provenance_reference: str = Field(min_length=1)

    @field_validator("available_at")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)


class CommitteeConflict(_CommitteeModel):
    conflict_id: str = Field(min_length=1)
    conflict_type: str = Field(min_length=1)
    roles: tuple[CommitteeMemberRole, ...]
    description: str = Field(min_length=1)
    severity: str = Field(pattern=r"^(low|moderate|high|critical)$")
    unresolved: bool
    evidence_references: tuple[str, ...] = ()
    recommended_followup: str = Field(min_length=1)


class CommitteeQuestion(_CommitteeModel):
    question_id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    description: str = Field(min_length=1)
    requested_from: tuple[CommitteeMemberRole, ...]
    priority: str = Field(pattern=r"^(low|moderate|high|critical)$")
    blocking: bool
    evidence_gap: str = Field(min_length=1)


class CommitteeDissent(_CommitteeModel):
    dissenting_role: CommitteeMemberRole
    view: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    reason: str = Field(min_length=1)
    severity: str = Field(pattern=r"^(low|moderate|high|critical)$")
    acknowledged: bool
    blocking: bool


class CommitteeAssessment(_ResearchOnly):
    assessment_id: str = Field(min_length=1)
    as_of: UtcDatetime
    symbol_or_scope: str = Field(min_length=1)
    member_views: tuple[CommitteeMemberView, ...]
    research_alignment: float = Field(ge=0, le=1, allow_inf_nan=False)
    research_dispersion: float = Field(ge=0, le=1, allow_inf_nan=False)
    portfolio_proposal_id: str = Field(min_length=1)
    risk_assessment_id: str = Field(min_length=1)
    risk_decision: RiskDecisionType
    conflicts: tuple[CommitteeConflict, ...] = ()
    unanswered_questions: tuple[CommitteeQuestion, ...] = ()
    coverage_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    freshness_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    data_quality_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    committee_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    warnings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    provenance: dict[str, Any]
    trace: AnalysisTrace

    @field_validator("as_of")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)


class CommitteeRecommendationType(StrEnum):
    APPROVE_RESEARCH_PROPOSAL = "approve_research_proposal"
    REVISE_RESEARCH_PROPOSAL = "revise_research_proposal"
    REJECT_RESEARCH_PROPOSAL = "reject_research_proposal"
    DEFER = "defer"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class CommitteeRecommendation(_ResearchOnly):
    recommendation_id: str = Field(min_length=1)
    assessment_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    recommendation: CommitteeRecommendationType
    rationale: tuple[str, ...]
    supporting_views: tuple[CommitteeMemberView, ...] = ()
    dissenting_views: tuple[CommitteeDissent, ...] = ()
    blocking_risk_findings: tuple[str, ...] = ()
    required_modifications: tuple[str, ...] = ()
    unanswered_questions: tuple[CommitteeQuestion, ...] = ()
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    warnings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    requires_chairman_review: Literal[True] = True

    @model_validator(mode="after")
    def risk_precedence(self) -> CommitteeRecommendation:
        if self.blocking_risk_findings and self.recommendation is CommitteeRecommendationType.APPROVE_RESEARCH_PROPOSAL:
            raise ValueError("blocking risk findings prevent approval")
        return self
