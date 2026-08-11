"""Deterministic governance review; this module performs no investment analysis."""

from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.chairman import ChairmanFinding
from app.domain.models.committee import CommitteeAssessment, CommitteeMemberRole, CommitteeRecommendation
from app.domain.models.portfolio import PortfolioProposal
from app.domain.models.risk import RiskAssessment, RiskDecision, RiskDecisionType
from app.services.chairman.config import ChairmanConfig


class GovernanceReviewService:
    def evaluate(
        self,
        committee: CommitteeAssessment,
        recommendation: CommitteeRecommendation,
        proposal: PortfolioProposal,
        risk_assessment: RiskAssessment,
        risk_decision: RiskDecision,
        as_of: datetime,
        config: ChairmanConfig,
    ) -> tuple[ChairmanFinding, ...]:
        findings: list[ChairmanFinding] = []

        def add(category: str, severity: str, summary: str, rationale: str, refs: tuple[str, ...]) -> None:
            key = f"{category}:{severity}:{summary}:{'|'.join(refs)}"
            findings.append(
                ChairmanFinding(
                    finding_id=str(uuid5(NAMESPACE_URL, key)),
                    category=category,
                    severity=severity,
                    summary=summary,
                    rationale=rationale,
                    evidence_refs=refs,
                )
            )

        required = {role for role in CommitteeMemberRole}
        present = {view.role for view in committee.member_views}
        if config.require_all_specialists and not required.issubset(present):
            add(
                "committee_completeness",
                "high",
                "Required committee roles are missing",
                "All governance roles must be represented.",
                (committee.assessment_id,),
            )
        if committee.coverage_score < 1.0:
            add(
                "committee_completeness",
                "high",
                "Committee coverage is incomplete",
                "Incomplete coverage cannot support Chairman approval.",
                (committee.assessment_id,),
            )
        if risk_decision.decision is RiskDecisionType.REJECT:
            add(
                "risk_precedence",
                "critical",
                "Risk Officer rejected the proposal",
                "A Risk REJECT can never be overridden.",
                (risk_decision.decision_id,),
            )
        elif risk_decision.decision is RiskDecisionType.INSUFFICIENT_DATA:
            add(
                "risk_precedence",
                "critical",
                "Risk evidence is insufficient",
                "Insufficient risk data can never become approval.",
                (risk_decision.decision_id,),
            )
        elif risk_decision.decision is RiskDecisionType.REQUIRE_MODIFICATION:
            add(
                "risk_precedence",
                "high",
                "Risk modifications remain required",
                "Required risk modifications must return through governance.",
                (risk_decision.decision_id,),
            )
        for conflict in committee.conflicts:
            if conflict.unresolved:
                add(
                    "unresolved_conflict",
                    "critical" if conflict.severity == "critical" else "high",
                    conflict.description,
                    "Unresolved committee conflict prevents approval.",
                    (conflict.conflict_id,),
                )
        if recommendation.dissenting_views and any(not item.acknowledged for item in recommendation.dissenting_views):
            add(
                "dissent",
                "high",
                "Committee dissent is not acknowledged",
                "Governance must record and acknowledge dissent.",
                (recommendation.recommendation_id,),
            )
        if recommendation.proposal_id != proposal.proposal_id:
            add(
                "portfolio_consistency",
                "critical",
                "Proposal reference is inconsistent",
                "The reviewed portfolio must be identical across outputs.",
                (proposal.proposal_id,),
            )
        if as_of - committee.as_of > config.maximum_input_age:
            add(
                "data_freshness",
                "high",
                "Committee output is stale",
                "Stale governance evidence cannot support approval.",
                (committee.assessment_id,),
            )
        if committee.data_quality_score < 0.70 or risk_assessment.data_quality_score < 0.70:
            add(
                "data_quality",
                "high",
                "Underlying data quality is inadequate",
                "Governance requires dependable underlying evidence.",
                (committee.assessment_id, risk_assessment.assessment_id),
            )
        required_provenance = {
            "input_fingerprint",
            "portfolio_proposal_id",
            "risk_assessment_id",
            "risk_decision_id",
            "committee_service_version",
        }
        if config.require_complete_provenance and (
            not required_provenance.issubset(committee.provenance)
            or any(committee.provenance.get(key) is None for key in required_provenance)
        ):
            add(
                "provenance",
                "high",
                "Committee provenance is incomplete",
                "Complete provenance is required for accountable governance.",
                (committee.assessment_id,),
            )
        if not risk_assessment.provenance:
            add(
                "provenance",
                "high",
                "Risk provenance is missing",
                "The Risk Officer evidence must be attributable.",
                (risk_assessment.assessment_id,),
            )
        required_nodes = {"CommitteeAssessment", "RiskDecision"}
        node_types = {node.node_type for node in committee.trace.nodes}
        if config.require_complete_trace and not required_nodes.issubset(node_types):
            add("trace", "high", "Committee trace is incomplete", "The governance chain must be auditable.", (committee.trace.trace_id,))
        if committee.unanswered_questions or recommendation.unanswered_questions:
            add(
                "missing_evidence",
                "high",
                "Committee questions remain unanswered",
                "Blocking evidence gaps require resolution.",
                (committee.assessment_id,),
            )
        return tuple(findings)
