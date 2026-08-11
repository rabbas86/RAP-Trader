"""Offline Investment Committee orchestration."""

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.analyst import AnalystOpinion
from app.domain.models.committee import CommitteeAssessment, CommitteeMemberRole, CommitteeMemberView, CommitteeRecommendation
from app.domain.models.portfolio import PortfolioProposal
from app.domain.models.risk import RiskAssessment, RiskDecision
from app.services.committee.alignment import CommitteeAlignmentService
from app.services.committee.config import CommitteeConfig
from app.services.committee.conflicts import CommitteeConflictService
from app.services.committee.deliberation import CommitteeDeliberationService
from app.services.committee.portfolio_review import CommitteePortfolioReviewService
from app.services.committee.provenance import CommitteeProvenanceService
from app.services.committee.questions import CommitteeQuestionService
from app.services.committee.research_case import ResearchCaseAssemblyService
from app.services.committee.risk_governance import CommitteeRiskGovernanceService
from app.services.committee.trace import build_committee_trace
from app.services.committee.validation import CommitteeInputValidationService


class InvestmentCommitteeService:
    def __init__(self, config: CommitteeConfig | None = None) -> None:
        self.config = config or CommitteeConfig()
        self.provenance = CommitteeProvenanceService()

    def health(self) -> dict[str, object]:
        return {"status": "ok", "offline": True, "research_only": True, "checked_at": datetime.now(UTC).isoformat()}

    def metadata(self) -> dict[str, object]:
        return {
            "component": "investment-committee",
            "service_version": self.config.service_version,
            "deterministic": True,
            "offline": True,
            "research_only": True,
            "suitable_for_live_trading": False,
            "decision_ready": False,
            "output": "CommitteeAssessment and CommitteeRecommendation",
        }

    def _required_roles(self) -> tuple[CommitteeMemberRole, ...]:
        allowed = {
            CommitteeMemberRole.NEWS_ANALYST: self.config.allow_missing_news,
            CommitteeMemberRole.MACRO_ECONOMIST: self.config.allow_missing_macro,
            CommitteeMemberRole.FUNDAMENTAL_ANALYST: self.config.allow_missing_fundamental,
            CommitteeMemberRole.TECHNICAL_ANALYST: self.config.allow_missing_technical,
        }
        return tuple(role for role in self.config.required_specialist_roles if not allowed.get(role, False))

    def assess(
        self,
        opinions: list[AnalystOpinion],
        proposal: PortfolioProposal,
        risk_assessment: RiskAssessment,
        risk_decision: RiskDecision,
        as_of: datetime | None = None,
    ) -> CommitteeAssessment:
        review_at = as_of or proposal.as_of
        CommitteeInputValidationService().validate(opinions, proposal, risk_assessment, risk_decision, review_at, self.config)
        required_roles = self._required_roles()
        case = ResearchCaseAssemblyService().assemble(opinions, required_roles)
        alignment = CommitteeAlignmentService().calculate(case, len(required_roles), self.config.dissent_escalation_threshold)
        portfolio = CommitteePortfolioReviewService().review(proposal, case, self.config)
        risk = CommitteeRiskGovernanceService().evaluate(risk_assessment, risk_decision)
        conflicts = CommitteeConflictService().identify(case, portfolio, risk)
        questions = CommitteeQuestionService().build(case, risk)
        source = self.provenance.fingerprint(
            {
                "opinions": opinions,
                "proposal": proposal,
                "risk_assessment": risk_assessment,
                "risk_decision": risk_decision,
                "policy": self.config,
            }
        )
        assessment_id = str(uuid5(NAMESPACE_URL, f"committee-assessment:{source}"))
        views = (
            *case.views,
            CommitteeMemberView(
                role=CommitteeMemberRole.PORTFOLIO_MANAGER,
                source_id=proposal.proposal_id,
                source_version=proposal.algorithm_version,
                direction_or_status="acceptable" if portfolio.acceptable else "revise",
                confidence=1.0 if portfolio.acceptable else 0.5,
                summary="Portfolio proposal governance review",
                warnings=portfolio.findings,
                limitations=(),
                available_at=proposal.as_of,
                provenance_reference=f"proposal:{proposal.proposal_id}",
            ),
            CommitteeMemberView(
                role=CommitteeMemberRole.RISK_OFFICER,
                source_id=risk_decision.decision_id,
                source_version=str(risk_assessment.provenance.get("risk_service_version", "unspecified")),
                direction_or_status=risk_decision.decision.value,
                confidence=risk_assessment.data_quality_score,
                summary="Risk Officer decision",
                warnings=risk_decision.warnings,
                limitations=risk_assessment.limitations,
                available_at=risk_assessment.as_of,
                provenance_reference=f"risk-decision:{risk_decision.decision_id}",
            ),
        )
        confidence = max(
            0.0,
            min(
                1.0,
                (alignment.directional_agreement + alignment.coverage + alignment.freshness + risk_assessment.data_quality_score) / 4
                - 0.15 * len(alignment.strong_minority_roles),
            ),
        )
        provenance = {
            "input_fingerprint": source,
            "opinion_ids": tuple(item.opinion_id for item in opinions),
            "analyst_versions": {view.source_id: view.source_version for view in case.views},
            "portfolio_proposal_id": proposal.proposal_id,
            "portfolio_algorithm_version": proposal.algorithm_version,
            "risk_assessment_id": risk_assessment.assessment_id,
            "risk_decision_id": risk_decision.decision_id,
            "risk_service_version": risk_assessment.provenance.get("risk_service_version"),
            "committee_policy_fingerprint": self.provenance.fingerprint(self.config),
            "committee_service_version": self.config.service_version,
            "git_commit": self.provenance.git_commit(),
        }
        trace = build_committee_trace(
            tuple(item.opinion_id for item in opinions),
            proposal.proposal_id,
            risk_assessment.assessment_id,
            risk_decision.decision_id,
            assessment_id,
            review_at,
        )
        return CommitteeAssessment(
            assessment_id=assessment_id,
            as_of=review_at,
            symbol_or_scope=",".join(sorted({item.ticker for item in opinions})) or proposal.portfolio_id,
            member_views=views,
            research_alignment=alignment.directional_agreement,
            research_dispersion=alignment.disagreement,
            portfolio_proposal_id=proposal.proposal_id,
            risk_assessment_id=risk_assessment.assessment_id,
            risk_decision=risk_decision.decision,
            conflicts=conflicts,
            unanswered_questions=questions,
            coverage_score=alignment.coverage,
            freshness_score=alignment.freshness,
            data_quality_score=risk_assessment.data_quality_score,
            committee_confidence=confidence,
            warnings=tuple(item for view in views for item in view.warnings),
            limitations=("Offline deterministic research governance only",),
            provenance=provenance,
            trace=trace,
        )

    def recommend(
        self,
        assessment: CommitteeAssessment,
        opinions: list[AnalystOpinion],
        proposal: PortfolioProposal,
        risk_assessment: RiskAssessment,
        risk_decision: RiskDecision,
    ) -> CommitteeRecommendation:
        required_roles = self._required_roles()
        case = ResearchCaseAssemblyService().assemble(opinions, required_roles)
        alignment = CommitteeAlignmentService().calculate(case, len(required_roles), self.config.dissent_escalation_threshold)
        portfolio = CommitteePortfolioReviewService().review(proposal, case, self.config)
        risk = CommitteeRiskGovernanceService().evaluate(risk_assessment, risk_decision)
        return CommitteeDeliberationService().recommend(
            assessment.assessment_id,
            proposal.proposal_id,
            case,
            alignment,
            len(assessment.conflicts),
            sum(item.unresolved and item.severity in {"high", "critical"} for item in assessment.conflicts),
            portfolio,
            risk,
            assessment.data_quality_score,
            assessment.unanswered_questions,
            self.config,
        )

    def review(
        self,
        opinions: list[AnalystOpinion],
        proposal: PortfolioProposal,
        risk_assessment: RiskAssessment,
        risk_decision: RiskDecision,
        as_of: datetime | None = None,
    ) -> tuple[CommitteeAssessment, CommitteeRecommendation]:
        assessment = self.assess(opinions, proposal, risk_assessment, risk_decision, as_of)
        return assessment, self.recommend(assessment, opinions, proposal, risk_assessment, risk_decision)
