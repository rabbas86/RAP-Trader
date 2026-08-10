"""Shared fail-safe analyst output construction."""

from __future__ import annotations

from app.domain.models.analyst import (
    AnalysisDirection,
    AnalysisLimitation,
    AnalysisWarning,
    AnalystOpinion,
    AnalystRequest,
    AnalystRole,
    Assumption,
    EvidenceItem,
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
    *,
    warnings: list[AnalysisWarning] | None = None,
    limitations: list[AnalysisLimitation] | None = None,
    assumptions: list[Assumption] | None = None,
    evidence: list[EvidenceItem] | None = None,
    source: str = "insufficient_opinion",
) -> AnalystOpinion:
    """Construct a canonical INSUFFICIENT_EVIDENCE analyst opinion.

    Specialists may pass their own ``warnings`` / ``limitations`` /
    ``assumptions`` / ``evidence`` to preserve domain-specific context
    without duplicating AnalystOpinion construction.  When omitted the
    canonical defaults are used so there is exactly one construction path.
    """
    if warnings is None:
        warnings = [AnalysisWarning(code="INSUFFICIENT_DATA", message=reason)]
    if limitations is None:
        limitations = [AnalysisLimitation(code="NO_CONCLUSION", message="No specialist conclusion was produced")]
    if assumptions is None:
        assumptions = []
    if evidence is None:
        evidence = []

    return AnalystOpinion(
        opinion_id=opinion_id,
        analyst_id=analyst_id,
        analyst_role=role,
        ticker=request.ticker,
        direction=AnalysisDirection.INSUFFICIENT_EVIDENCE,
        confidence=confidence.assess(0),
        evidence=evidence,
        assumptions=assumptions,
        warnings=warnings,
        limitations=limitations,
        generated_at=request.as_of,
        data_freshness=freshness.assess(request.as_of, request.as_of, request.as_of, EvidenceType.OTHER),
        decision_ready=False,
        suitable_for_live_trading=False,
        research_only=True,
    )
