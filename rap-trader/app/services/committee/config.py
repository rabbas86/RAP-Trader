"""Conservative deterministic committee policy."""

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models.committee import CommitteeMemberRole


class CommitteeConfig(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    service_version: str = "phase-12-v1"
    required_specialist_roles: tuple[CommitteeMemberRole, ...] = (
        CommitteeMemberRole.TECHNICAL_ANALYST,
        CommitteeMemberRole.FUNDAMENTAL_ANALYST,
        CommitteeMemberRole.MACRO_ECONOMIST,
        CommitteeMemberRole.NEWS_ANALYST,
    )
    minimum_specialist_coverage: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)
    minimum_freshness_score: float = Field(default=0.75, ge=0, le=1, allow_inf_nan=False)
    minimum_data_quality: float = Field(default=0.70, ge=0, le=1, allow_inf_nan=False)
    minimum_committee_confidence: float = Field(default=0.60, ge=0, le=1, allow_inf_nan=False)
    maximum_unresolved_high_conflicts: int = Field(default=0, ge=0)
    allow_missing_news: bool = False
    allow_missing_macro: bool = False
    allow_missing_fundamental: bool = False
    allow_missing_technical: bool = False
    risk_approval_mandatory: bool = True
    unanimous_research_alignment_required: bool = False
    dissent_escalation_threshold: float = Field(default=0.75, ge=0, le=1, allow_inf_nan=False)
    maximum_as_of_delta_seconds: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    maximum_position_weight: float = Field(default=0.25, gt=0, le=1, allow_inf_nan=False)
