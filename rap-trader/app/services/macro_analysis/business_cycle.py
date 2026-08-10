"""Deterministic business-cycle analysis service.

Aggregates growth, employment, and inflation signals into a high-level
``BusinessCyclePhase`` classification.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.services.macro_analysis.base import MacroAnalysisService, MacroSignal, build_signal_id
from app.services.macro_analysis.config import MacroAnalystConfig
from app.services.macro_analysis.domain import (
    BUSINESS_CYCLE_CATEGORY,
    EmploymentTrend,
    GrowthTrend,
    InflationTrend,
)
from app.services.macro_analysis.observations import MacroObservation
from app.services.macro_analysis.trends import _map_business_cycle


class BusinessCycleService(MacroAnalysisService):
    category = BUSINESS_CYCLE_CATEGORY

    def __init__(self, config: MacroAnalystConfig | None = None) -> None:
        self.config = config or MacroAnalystConfig()

    def classify(
        self,
        observations: Sequence[MacroObservation],
        evaluated_at: datetime,
        *,
        growth: GrowthTrend = GrowthTrend.UNKNOWN,
        employment: EmploymentTrend = EmploymentTrend.UNKNOWN,
        inflation: InflationTrend = InflationTrend.UNKNOWN,
    ) -> MacroSignal | None:
        latest = self._latest(observations)
        if latest is None:
            return None
        phase = _map_business_cycle(growth, employment, inflation)
        confidence = self.config.base_evidence_confidence
        return MacroSignal(
            signal_id=build_signal_id(self.category, latest.series_id, evaluated_at),
            category=self.category,
            label=f"business cycle phase: {phase.value.lower()}",
            trend_enum=phase.value,
            latest_value=latest.value,
            latest_units=latest.units,
            delta=0.0,
            observed_at=latest.observed_at,
            available_at=latest.available_at,
            source=latest.source,
            source_fingerprint=latest.source_fingerprint,
            confidence=confidence,
            metadata={
                "series_id": latest.series_id,
                "growth": growth.value,
                "employment": employment.value,
                "inflation": inflation.value,
            },
        )

    def signal_id(self, observations: Sequence[MacroObservation], evaluated_at: datetime) -> str | None:
        latest = self._latest(observations)
        if latest is None:
            return None
        return build_signal_id(self.category, latest.series_id, evaluated_at)
