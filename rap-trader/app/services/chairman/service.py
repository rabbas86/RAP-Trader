"""Offline Chairman research-governance orchestration."""

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.chairman import ChairmanAssessment, ChairmanDecision, ChairmanDecisionType
from app.domain.models.committee import CommitteeAssessment, CommitteeRecommendation, CommitteeRecommendationType
from app.domain.models.portfolio import PortfolioProposal
from app.domain.models.risk import RiskAssessment, RiskDecision, RiskDecisionType
from app.services.chairman.config import ChairmanConfig
from app.services.chairman.governance import GovernanceReviewService
from app.services.chairman.provenance import ChairmanProvenanceService
from app.services.chairman.questions import ChairmanQuestionService
from app.services.chairman.trace import build_chairman_trace
from app.services.chairman.validation import ChairmanValidationService


class ChairmanService:
    def __init__(self, config: ChairmanConfig | None = None) -> None:
        self.config = config or ChairmanConfig()
        self.provenance = ChairmanProvenanceService()

    def health(self) -> dict[str, object]:
        return {"status": "ok", "offline": True, "research_only": True, "checked_at": datetime.now(UTC).isoformat()}

    def metadata(self) -> dict[str, object]:
        return {
            "component": "chairman",
            "service_version": self.config.service_version,
            "deterministic": True,
            "offline": True,
            "research_only": True,
            "suitable_for_live_trading": False,
            "decision_ready": False,
            "output": "ChairmanAssessment and ChairmanDecision",
            "execution_authority": False,
        }

    def assess(
        self,
        committee_assessment: CommitteeAssessment,
        recommendation: CommitteeRecommendation,
        proposal: PortfolioProposal,
        risk_assessment: RiskAssessment,
        risk_decision: RiskDecision,
        as_of: datetime | None = None,
    ) -> ChairmanAssessment:
        review_at = as_of or committee_assessment.as_of
        ChairmanValidationService().validate(
            committee_assessment, recommendation, proposal, risk_assessment, risk_decision, review_at, self.config
        )
        findings = GovernanceReviewService().evaluate(
            committee_assessment, recommendation, proposal, risk_assessment, risk_decision, review_at, self.config
        )
        questions = ChairmanQuestionService().build(findings)
        source = self.provenance.fingerprint(
            {
                "committee_assessment": committee_assessment,
                "recommendation": recommendation,
                "proposal": proposal,
                "risk_assessment": risk_assessment,
                "risk_decision": risk_decision,
                "policy": self.config,
            }
        )
        assessment_id = str(uuid5(NAMESPACE_URL, f"chairman-assessment:{source}"))
        decision_id = str(uuid5(NAMESPACE_URL, f"chairman-decision:{assessment_id}"))
        penalties = sum(0.35 if item.severity == "critical" else 0.20 if item.severity == "high" else 0.05 for item in findings)
        score = max(0.0, min(1.0, committee_assessment.committee_confidence - penalties))
        provenance = {
            "input_fingerprint": source,
            "committee_assessment_id": committee_assessment.assessment_id,
            "committee_recommendation_id": recommendation.recommendation_id,
            "portfolio_proposal_id": proposal.proposal_id,
            "risk_assessment_id": risk_assessment.assessment_id,
            "risk_decision_id": risk_decision.decision_id,
            "chairman_policy_fingerprint": self.provenance.fingerprint(self.config),
            "chairman_service_version": self.config.service_version,
            "git_commit": self.provenance.git_commit(),
        }
        return ChairmanAssessment(
            assessment_id=assessment_id,
            committee_assessment_id=committee_assessment.assessment_id,
            committee_recommendation_id=recommendation.recommendation_id,
            portfolio_proposal_id=proposal.proposal_id,
            risk_assessment_id=risk_assessment.assessment_id,
            risk_decision_id=risk_decision.decision_id,
            governance_score=score,
            governance_findings=findings,
            unresolved_questions=questions,
            warnings=tuple(item.summary for item in findings),
            limitations=("Research governance only; approval is not execution approval",),
            provenance=provenance,
            trace=build_chairman_trace(
                committee_assessment.assessment_id, recommendation.recommendation_id, assessment_id, decision_id, review_at
            ),
        )

    def decide(
        self, assessment: ChairmanAssessment, recommendation: CommitteeRecommendation, risk_decision: RiskDecision
    ) -> ChairmanDecision:
        critical_conflict = any(
            item.category == "unresolved_conflict" and item.severity == "critical" for item in assessment.governance_findings
        )
        missing_governance = assessment.governance_score < self.config.minimum_governance_score or any(
            question.blocking for question in assessment.unresolved_questions
        )
        if risk_decision.decision is RiskDecisionType.REJECT:
            decision = ChairmanDecisionType.REJECT_RESEARCH
        elif risk_decision.decision is RiskDecisionType.INSUFFICIENT_DATA or missing_governance:
            decision = ChairmanDecisionType.INSUFFICIENT_EVIDENCE
        elif critical_conflict:
            decision = ChairmanDecisionType.DEFER
        elif recommendation.recommendation is CommitteeRecommendationType.REJECT_RESEARCH_PROPOSAL:
            decision = ChairmanDecisionType.REJECT_RESEARCH
        elif recommendation.recommendation is CommitteeRecommendationType.INSUFFICIENT_EVIDENCE:
            decision = ChairmanDecisionType.INSUFFICIENT_EVIDENCE
        elif recommendation.recommendation is CommitteeRecommendationType.DEFER:
            decision = ChairmanDecisionType.DEFER
        elif recommendation.recommendation is CommitteeRecommendationType.REVISE_RESEARCH_PROPOSAL:
            decision = ChairmanDecisionType.REVISE_RESEARCH
        else:
            decision = ChairmanDecisionType.APPROVE_RESEARCH
        dissent = not any(item.category == "dissent" for item in assessment.governance_findings)
        return ChairmanDecision(
            decision_id=str(uuid5(NAMESPACE_URL, f"chairman-decision:{assessment.assessment_id}")),
            assessment_id=assessment.assessment_id,
            decision=decision,
            rationale=(f"Governance decision under {self.config.service_version}", *assessment.warnings),
            required_changes=tuple(question.description for question in assessment.unresolved_questions),
            acknowledged_dissent=dissent,
            acknowledged_risk=True,
            confidence=assessment.governance_score,
            warnings=assessment.warnings,
            limitations=assessment.limitations,
        )

    def review(
        self,
        committee_assessment: CommitteeAssessment,
        recommendation: CommitteeRecommendation,
        proposal: PortfolioProposal,
        risk_assessment: RiskAssessment,
        risk_decision: RiskDecision,
        as_of: datetime | None = None,
    ) -> tuple[ChairmanAssessment, ChairmanDecision]:
        assessment = self.assess(committee_assessment, recommendation, proposal, risk_assessment, risk_decision, as_of)
        return assessment, self.decide(assessment, recommendation, risk_decision)
