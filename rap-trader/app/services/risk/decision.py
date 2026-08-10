"""Deterministic research-only risk decision policy."""

from __future__ import annotations

from typing import ClassVar
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.risk import (
    RiskAssessment,
    RiskCategory,
    RiskDecision,
    RiskDecisionType,
    RiskModification,
    RiskModificationType,
    RiskSeverity,
)


class RiskDecisionService:
    MODIFICATION_TYPES: ClassVar[dict[RiskCategory, RiskModificationType]] = {
        RiskCategory.CONCENTRATION: RiskModificationType.REDUCE_SYMBOL_WEIGHT,
        RiskCategory.DIVERSIFICATION: RiskModificationType.REDUCE_SYMBOL_WEIGHT,
        RiskCategory.SECTOR: RiskModificationType.REDUCE_SECTOR_EXPOSURE,
        RiskCategory.INDUSTRY: RiskModificationType.REDUCE_INDUSTRY_EXPOSURE,
        RiskCategory.ASSET_CLASS: RiskModificationType.REDUCE_ASSET_CLASS_EXPOSURE,
        RiskCategory.CASH: RiskModificationType.INCREASE_CASH,
        RiskCategory.GROSS_EXPOSURE: RiskModificationType.REDUCE_GROSS_EXPOSURE,
        RiskCategory.LEVERAGE: RiskModificationType.REDUCE_GROSS_EXPOSURE,
        RiskCategory.NET_EXPOSURE: RiskModificationType.REDUCE_NET_EXPOSURE,
        RiskCategory.SHORT_EXPOSURE: RiskModificationType.REDUCE_SHORT_EXPOSURE,
        RiskCategory.TURNOVER: RiskModificationType.REDUCE_TURNOVER,
        RiskCategory.CORRELATION: RiskModificationType.REDUCE_CORRELATED_CLUSTER,
        RiskCategory.LIQUIDITY: RiskModificationType.REMOVE_OR_REDUCE_ILLIQUID_ASSET,
        RiskCategory.DATA_QUALITY: RiskModificationType.IMPROVE_DATA_QUALITY,
        RiskCategory.STALE_DATA: RiskModificationType.REFRESH_STALE_DATA,
        RiskCategory.VAR: RiskModificationType.REDUCE_VAR,
        RiskCategory.CVAR: RiskModificationType.REDUCE_CVAR,
        RiskCategory.VOLATILITY: RiskModificationType.REDUCE_VOLATILITY,
        RiskCategory.DRAWDOWN: RiskModificationType.REDUCE_DRAWDOWN_EXPOSURE,
    }

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
                modification_type=self.MODIFICATION_TYPES.get(breach.category, RiskModificationType.OTHER),
                category=breach.category,
                current_value=breach.observed_value,
                recommended_maximum=(
                    None if breach.metric_name in {"cash_weight", "effective_positions", "liquidity_score"} else breach.threshold
                ),
                recommended_minimum=(
                    breach.threshold if breach.metric_name in {"cash_weight", "effective_positions", "liquidity_score"} else None
                ),
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
