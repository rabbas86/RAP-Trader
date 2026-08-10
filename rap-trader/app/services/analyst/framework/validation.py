"""Canonical analyst evidence validation."""

from datetime import datetime

from app.domain.models.analyst import AnalystError, AnalystErrorCodes, EvidenceItem
from app.services.analyst.framework.freshness import DataFreshnessService


class EvidenceValidationService:
    def __init__(self, freshness: DataFreshnessService | None = None) -> None:
        self.freshness = freshness or DataFreshnessService()

    def validate(self, items: list[EvidenceItem], as_of: datetime, *, allow_stale: bool = False) -> None:
        ids = [item.evidence_id for item in items]
        if len(ids) != len(set(ids)):
            raise AnalystError(AnalystErrorCodes.INVALID_REQUEST, "Evidence IDs must be unique")
        for item in items:
            if item.available_at > as_of:
                raise AnalystError(AnalystErrorCodes.LOOKAHEAD_REJECTED, "Evidence was not available at the evaluation time")
            assessment = self.freshness.assess(item.observed_at, item.available_at, as_of, item.evidence_type)
            if (assessment.is_stale or item.valid_until < as_of) and not allow_stale:
                raise AnalystError(AnalystErrorCodes.FRESHNESS_REJECTED, "Expired or stale evidence was rejected")
