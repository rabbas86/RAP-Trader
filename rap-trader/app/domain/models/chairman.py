"""Strict contracts for the offline, research-only Chairman authority."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models.analyst import AnalysisTrace


class _ChairmanModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)


class _ResearchOnly(_ChairmanModel):
    research_only: Literal[True] = True
    suitable_for_live_trading: Literal[False] = False
    decision_ready: Literal[False] = False


class ChairmanDecisionType(StrEnum):
    APPROVE_RESEARCH = "approve_research"
    REVISE_RESEARCH = "revise_research"
    REJECT_RESEARCH = "reject_research"
    DEFER = "defer"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ChairmanFinding(_ChairmanModel):
    finding_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    severity: str = Field(pattern=r"^(info|low|moderate|high|critical)$")
    summary: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()


class ChairmanQuestion(_ChairmanModel):
    question_id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    blocking: bool
    assigned_role: str = Field(min_length=1)
    description: str = Field(min_length=1)


class ChairmanAssessment(_ResearchOnly):
    assessment_id: str = Field(min_length=1)
    committee_assessment_id: str = Field(min_length=1)
    committee_recommendation_id: str = Field(min_length=1)
    portfolio_proposal_id: str = Field(min_length=1)
    risk_assessment_id: str = Field(min_length=1)
    risk_decision_id: str = Field(min_length=1)
    governance_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    governance_findings: tuple[ChairmanFinding, ...]
    unresolved_questions: tuple[ChairmanQuestion, ...]
    warnings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    provenance: dict[str, Any]
    trace: AnalysisTrace


class ChairmanDecision(_ResearchOnly):
    decision_id: str = Field(min_length=1)
    assessment_id: str = Field(min_length=1)
    decision: ChairmanDecisionType
    rationale: tuple[str, ...]
    required_changes: tuple[str, ...]
    acknowledged_dissent: bool
    acknowledged_risk: bool
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    warnings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
