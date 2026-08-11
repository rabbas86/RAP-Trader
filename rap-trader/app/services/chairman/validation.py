"""Point-in-time and referential Chairman validation."""

from datetime import datetime
from enum import StrEnum

from app.domain.models.committee import CommitteeAssessment, CommitteeRecommendation
from app.domain.models.portfolio import PortfolioProposal
from app.domain.models.risk import RiskAssessment, RiskDecision
from app.services.chairman.config import ChairmanConfig


class ChairmanDecisionErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_CHAIRMAN_INPUT"
    FUTURE_DATA = "FUTURE_CHAIRMAN_DATA"
    MISSING_COMMITTEE = "MISSING_COMMITTEE_OUTPUT"
    MISMATCHED_REFERENCE = "MISMATCHED_CHAIRMAN_REFERENCE"
    INCOMPLETE_PROVENANCE = "INCOMPLETE_CHAIRMAN_PROVENANCE"


class ChairmanDecisionError(ValueError):
    def __init__(self, code: ChairmanDecisionErrorCode, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


ChairmanErrorCode = ChairmanDecisionErrorCode
ChairmanError = ChairmanDecisionError


class ChairmanValidationService:
    def validate(
        self,
        committee_assessment: CommitteeAssessment,
        recommendation: CommitteeRecommendation,
        proposal: PortfolioProposal,
        risk_assessment: RiskAssessment,
        risk_decision: RiskDecision,
        as_of: datetime,
        config: ChairmanConfig,
    ) -> None:
        if (not committee_assessment or not recommendation) and not config.allow_missing_committee:
            raise ChairmanDecisionError(ChairmanDecisionErrorCode.MISSING_COMMITTEE, "Committee output is required")
        if recommendation.assessment_id != committee_assessment.assessment_id:
            raise ChairmanDecisionError(
                ChairmanDecisionErrorCode.MISMATCHED_REFERENCE, "Committee recommendation does not match assessment"
            )
        if recommendation.proposal_id != proposal.proposal_id or committee_assessment.portfolio_proposal_id != proposal.proposal_id:
            raise ChairmanDecisionError(ChairmanDecisionErrorCode.MISMATCHED_REFERENCE, "Committee output does not match proposal")
        if committee_assessment.risk_assessment_id != risk_assessment.assessment_id:
            raise ChairmanDecisionError(ChairmanDecisionErrorCode.MISMATCHED_REFERENCE, "Committee output does not match risk assessment")
        if risk_decision.assessment_id != risk_assessment.assessment_id or risk_decision.proposal_id != proposal.proposal_id:
            raise ChairmanDecisionError(ChairmanDecisionErrorCode.MISMATCHED_REFERENCE, "Risk references do not match Chairman review")
        timestamps = (proposal.as_of, risk_assessment.as_of, committee_assessment.as_of)
        if any(item > as_of for item in timestamps):
            raise ChairmanDecisionError(ChairmanDecisionErrorCode.FUTURE_DATA, "Future Chairman inputs are forbidden")
        if (max(timestamps) - min(timestamps)).total_seconds() > config.maximum_as_of_delta_seconds:
            raise ChairmanDecisionError(ChairmanDecisionErrorCode.INVALID_INPUT, "Chairman inputs have incompatible as-of timestamps")
