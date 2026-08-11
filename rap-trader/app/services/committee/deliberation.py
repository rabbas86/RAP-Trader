"""Deterministic committee synthesis with explicit governance precedence."""

from uuid import NAMESPACE_URL, uuid5

from app.domain.models.committee import CommitteeQuestion, CommitteeRecommendation, CommitteeRecommendationType
from app.services.committee.alignment import CommitteeAlignment
from app.services.committee.config import CommitteeConfig
from app.services.committee.dissent import CommitteeDissentService
from app.services.committee.portfolio_review import PortfolioReview
from app.services.committee.research_case import ResearchCase
from app.services.committee.risk_governance import RiskGovernance


class CommitteeDeliberationService:
    def recommend(
        self,
        assessment_id: str,
        proposal_id: str,
        case: ResearchCase,
        alignment: CommitteeAlignment,
        conflicts_count: int,
        high_conflicts: int,
        portfolio: PortfolioReview,
        risk: RiskGovernance,
        questions: tuple[CommitteeQuestion, ...],
        config: CommitteeConfig,
    ) -> CommitteeRecommendation:
        dissent = CommitteeDissentService().identify(case, alignment)
        confidence = max(
            0.0,
            min(
                1.0,
                (alignment.directional_agreement + alignment.coverage + alignment.freshness) / 3
                - 0.15 * len(tuple(item for item in dissent if item.severity == "high")),
            ),
        )
        rationale: list[str] = []
        recommendation = risk.forced_recommendation
        if recommendation is not None:
            rationale.append(f"Risk Officer precedence requires {recommendation.value}")
        elif case.missing_roles or alignment.coverage < config.minimum_specialist_coverage:
            recommendation = CommitteeRecommendationType.INSUFFICIENT_EVIDENCE
            rationale.append("Mandatory specialist coverage is incomplete")
        elif high_conflicts > config.maximum_unresolved_high_conflicts:
            recommendation = CommitteeRecommendationType.DEFER
            rationale.append("Unresolved high-severity conflicts exceed policy")
        elif alignment.freshness < config.minimum_freshness_score:
            recommendation = CommitteeRecommendationType.DEFER
            rationale.append("Research freshness is below policy")
        elif not portfolio.acceptable:
            recommendation = CommitteeRecommendationType.REVISE_RESEARCH_PROPOSAL
            rationale.append("Portfolio proposal requires committee modifications")
        elif alignment.disagreement > 0.25 or (config.unanimous_research_alignment_required and alignment.directional_agreement < 1):
            recommendation = CommitteeRecommendationType.DEFER
            rationale.append("Research alignment is insufficient for approval")
        elif confidence < config.minimum_committee_confidence:
            recommendation = CommitteeRecommendationType.DEFER
            rationale.append("Committee confidence is below policy")
        else:
            recommendation = CommitteeRecommendationType.APPROVE_RESEARCH_PROPOSAL
            rationale.append("Research, portfolio, and approved risk review satisfy committee policy")
        modifications = tuple(dict.fromkeys((*risk.required_modifications, *portfolio.required_modifications)))
        return CommitteeRecommendation(
            recommendation_id=str(uuid5(NAMESPACE_URL, f"committee-recommendation:{assessment_id}:{recommendation.value}")),
            assessment_id=assessment_id,
            proposal_id=proposal_id,
            recommendation=recommendation,
            rationale=tuple(rationale),
            supporting_views=tuple(view for view in case.views if view.direction_or_status == alignment.majority_direction.value),
            dissenting_views=dissent,
            blocking_risk_findings=risk.blocking_findings,
            required_modifications=modifications,
            unanswered_questions=questions,
            confidence=confidence,
            warnings=(f"{conflicts_count} unresolved committee conflict(s)",) if conflicts_count else (),
            limitations=("Committee approval is research governance only and never execution approval",),
        )
