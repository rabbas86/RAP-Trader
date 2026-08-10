"""Deterministic research-only risk decision policy."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from app.domain.models.risk import RiskAssessment, RiskDecision, RiskDecisionType, RiskModification, RiskSeverity


class RiskDecisionService:
    def decide(self, assessment: RiskAssessment, catastrophic_stress_loss: float, minimum_quality: float) -> RiskDecision:
        catastrophic = any(result.estimated_portfolio_impact < -catastrophic_stress_loss for result in assessment.stress_results)
        critical_hard = [item for item in assessment.breaches if item.hard_limit and item.severity is RiskSeverity.CRITICAL]
        severe = [item for item in assessment.breaches if item.severity in {RiskSeverity.HIGH, RiskSeverity.CRITICAL}]
        if assessment.data_quality_score < minimum_quality or assessment.limitations:
            decision = RiskDecisionType.INSUFFICIENT_DATA
            rationale = ("Required risk inputs are incomplete, stale, or insufficient",)
        elif critical_hard or catastrophic or len(severe) >= 3:
            decision = RiskDecisionType.REJECT
            rationale = ("One or more non-overridable portfolio risk bounds were exceeded",)
        elif assessment.breaches:
            decision = RiskDecisionType.REQUIRE_MODIFICATION
            rationale = ("The proposal is plausibly correctable but exceeds research risk limits",)
        else:
            decision = RiskDecisionType.APPROVE
            rationale = ("No configured portfolio risk limit was exceeded",)
        modifications = tuple(
            RiskModification(
                modification_type="reduce_risk",
                category=breach.category,
                current_value=breach.observed_value,
                recommended_maximum=breach.threshold,
                reason=breach.recommended_action,
                associated_breach_ids=(breach.breach_id,),
            )
            for breach in assessment.breaches
        )
        identifier = str(uuid5(NAMESPACE_URL, f"risk-decision:{assessment.assessment_id}:{decision.value}"))
        return RiskDecision(
            decision_id=identifier,
            assessment_id=assessment.assessment_id,
            proposal_id=assessment.proposal_id,
            decision=decision,
            rationale=rationale,
            required_modifications=modifications,
            blocking_breaches=tuple(item.breach_id for item in critical_hard),
            warnings=assessment.warnings,
        )
