"""Non-overridable Risk Officer precedence."""

from pydantic import BaseModel, ConfigDict

from app.domain.models.committee import CommitteeRecommendationType
from app.domain.models.risk import RiskAssessment, RiskDecision, RiskDecisionType


class RiskGovernance(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)
    decision: RiskDecisionType
    forced_recommendation: CommitteeRecommendationType | None
    blocking_findings: tuple[str, ...]
    required_modifications: tuple[str, ...]


class CommitteeRiskGovernanceService:
    def evaluate(self, assessment: RiskAssessment, decision: RiskDecision) -> RiskGovernance:
        forced = {
            RiskDecisionType.REJECT: CommitteeRecommendationType.REJECT_RESEARCH_PROPOSAL,
            RiskDecisionType.INSUFFICIENT_DATA: CommitteeRecommendationType.INSUFFICIENT_EVIDENCE,
            RiskDecisionType.REQUIRE_MODIFICATION: CommitteeRecommendationType.REVISE_RESEARCH_PROPOSAL,
        }.get(decision.decision)
        blocking = decision.blocking_breaches
        if decision.decision is RiskDecisionType.INSUFFICIENT_DATA and not blocking:
            blocking = ("Risk Officer reported insufficient data",)
        modifications = tuple(item.reason for item in decision.required_modifications)
        return RiskGovernance(
            decision=decision.decision, forced_recommendation=forced, blocking_findings=blocking, required_modifications=modifications
        )
