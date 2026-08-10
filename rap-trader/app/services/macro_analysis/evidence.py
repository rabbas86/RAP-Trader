"""Conversion of macro signals into shared Phase 5 evidence contracts.

``MacroEvidenceFactory`` turns ``MacroSignal`` objects into
``EvidenceItem`` records suitable for the Phase 5 analyst-opinion lifecycle.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.analyst import (
    AnalysisLimitation,
    AnalysisWarning,
    Assumption,
    EvidenceItem,
    EvidenceStrength,
    ProvenanceRecord,
)
from app.services.macro_analysis.base import MacroSignal
from app.services.macro_analysis.config import MacroAnalystConfig
from app.services.macro_analysis.domain import MACRO_EVIDENCE_TYPE


class MacroEvidenceFactory:
    """Build Phase-5 ``EvidenceItem`` objects from macro signals."""

    def __init__(self, config: MacroAnalystConfig | None = None) -> None:
        self.config = config or MacroAnalystConfig()

    def create(self, signal: MacroSignal, evaluated_at: datetime, source: str) -> EvidenceItem:
        evidence_type = MACRO_EVIDENCE_TYPE
        observed = signal.observed_at
        available = signal.available_at
        warnings = (
            [AnalysisWarning(code="MACRO_WARNING", message=signal.trend_enum)]
            if signal.trend_enum
            in {
                "UNKNOWN",
                "STABLE",
            }
            else []
        )
        strength = (
            EvidenceStrength.STRONG
            if signal.confidence >= 0.75
            else (EvidenceStrength.MODERATE if signal.confidence >= 0.5 else EvidenceStrength.WEAK)
        )
        evidence_id = str(uuid5(NAMESPACE_URL, f"macro|{signal.signal_id}|{evaluated_at.isoformat()}"))
        return EvidenceItem(
            evidence_id=evidence_id,
            evidence_type=evidence_type,
            observed_at=observed,
            available_at=available,
            evaluated_at=evaluated_at,
            valid_until=evaluated_at + timedelta(days=3),
            strength=strength,
            summary=f"{signal.category}: {signal.label}",
            confidence=round(signal.confidence, 6),
            capped=False,
            calibration_status="uncalibrated deterministic macro formula",
            has_historical_calibration=False,
            source_analyst="macro",
            assumptions=[Assumption(description="Macro observations are point-in-time deterministic snapshots")],
            warnings=warnings,
            limitations=[
                AnalysisLimitation(
                    code="MACRO_LIMITATION",
                    message="Macro regimes are based on deterministic thresholds and may not capture structural shifts",
                )
            ],
            provenance=[ProvenanceRecord(source=source, retrieved_at=available, uri=None)],
        )

    def build(self, signals: list[MacroSignal], evaluated_at: datetime, source: str) -> list[EvidenceItem]:
        return [self.create(signal, evaluated_at, source) for signal in signals]
