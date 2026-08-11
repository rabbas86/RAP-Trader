"""Point-in-time and referential committee validation."""

from datetime import datetime
from enum import StrEnum

from app.domain.models.analyst import AnalystOpinion, AnalystRole
from app.domain.models.portfolio import PortfolioProposal
from app.domain.models.risk import RiskAssessment, RiskDecision
from app.services.committee.config import CommitteeConfig


class CommitteeErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_COMMITTEE_INPUT"
    FUTURE_DATA = "FUTURE_COMMITTEE_DATA"
    DUPLICATE_SPECIALIST = "DUPLICATE_SPECIALIST"
    MISSING_SPECIALIST = "MISSING_SPECIALIST"
    STALE_INPUT = "STALE_COMMITTEE_INPUT"
    MISMATCHED_REFERENCE = "MISMATCHED_COMMITTEE_REFERENCE"


class CommitteeError(ValueError):
    def __init__(self, code: CommitteeErrorCode, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


_SPECIALISTS = {AnalystRole.TECHNICAL, AnalystRole.FUNDAMENTAL, AnalystRole.MACRO, AnalystRole.NEWS}


class CommitteeInputValidationService:
    def validate(
        self,
        opinions: list[AnalystOpinion],
        proposal: PortfolioProposal,
        assessment: RiskAssessment,
        decision: RiskDecision,
        as_of: datetime,
        config: CommitteeConfig,
    ) -> tuple[AnalystRole, ...]:
        ids = [item.opinion_id for item in opinions]
        if len(ids) != len(set(ids)):
            raise CommitteeError(CommitteeErrorCode.INVALID_INPUT, "Opinion IDs must be unique")
        roles = [item.analyst_role for item in opinions if item.analyst_role in _SPECIALISTS]
        if len(roles) != len(set(roles)):
            raise CommitteeError(CommitteeErrorCode.DUPLICATE_SPECIALIST, "Expected specialist roles must be unique")
        timestamps = [item.generated_at for item in opinions] + [proposal.as_of, assessment.as_of]
        if any(timestamp > as_of for timestamp in timestamps):
            raise CommitteeError(CommitteeErrorCode.FUTURE_DATA, "Future committee inputs are forbidden")
        if any(abs((timestamp - as_of).total_seconds()) > config.maximum_as_of_delta_seconds for timestamp in timestamps):
            raise CommitteeError(CommitteeErrorCode.INVALID_INPUT, "Committee inputs have incompatible as-of timestamps")
        if any(item.data_freshness.is_stale for item in opinions):
            raise CommitteeError(CommitteeErrorCode.STALE_INPUT, "Stale specialist inputs violate committee policy")
        if assessment.proposal_id != proposal.proposal_id:
            raise CommitteeError(CommitteeErrorCode.MISMATCHED_REFERENCE, "Risk assessment does not refer to the supplied proposal")
        if decision.assessment_id != assessment.assessment_id or decision.proposal_id != proposal.proposal_id:
            raise CommitteeError(CommitteeErrorCode.MISMATCHED_REFERENCE, "Risk decision references do not match the review")
        return tuple(sorted(_SPECIALISTS - set(roles), key=lambda role: role.value))
