"""Shared fail-safe analyst output construction."""

from app.domain.models.analyst import (
    AnalysisDirection,
    AnalysisLimitation,
    AnalysisWarning,
    AnalystOpinion,
    AnalystRequest,
    AnalystRole,
    EvidenceType,
)
from app.services.analyst.framework.confidence import ConfidenceAssessmentService
from app.services.analyst.framework.freshness import DataFreshnessService


def insufficient_opinion(
    opinion_id: str,
    analyst_id: str,
    role: AnalystRole,
    request: AnalystRequest,
    reason: str,
    freshness: DataFreshnessService,
    confidence: ConfidenceAssessmentService,
) -> AnalystOpinion:
    return AnalystOpinion(
        opinion_id=opinion_id,
        analyst_id=analyst_id,
        analyst_role=role,
        ticker=request.ticker,
        direction=AnalysisDirection.INSUFFICIENT_EVIDENCE,
        confidence=confidence.assess(0),
        evidence=[],
        warnings=[AnalysisWarning(code="INSUFFICIENT_DATA", message=reason)],
        limitations=[AnalysisLimitation(code="NO_CONCLUSION", message="No specialist conclusion was produced")],
        generated_at=request.as_of,
        data_freshness=freshness.assess(request.as_of, request.as_of, request.as_of, EvidenceType.OTHER),
    )
