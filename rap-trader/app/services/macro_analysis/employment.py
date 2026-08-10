"""Deterministic employment analysis service.

Classifies the unemployment and nonfarm-payrolls series into an
``EmploymentTrend`` (strengthening, weakening, or stable).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.services.macro_analysis.base import MacroAnalysisService, MacroSignal, build_signal_id
from app.services.macro_analysis.config import MacroAnalystConfig
from app.services.macro_analysis.domain import EMPLOYMENT_CATEGORY
from app.services.macro_analysis.observations import MacroObservation
from app.services.macro_analysis.trends import _map_employment


class EmploymentAnalysisService(MacroAnalysisService):
    category = EMPLOYMENT_CATEGORY

    def __init__(self, config: MacroAnalystConfig | None = None) -> None:
        self.config = config or MacroAnalystConfig()

    def classify(self, observations: Sequence[MacroObservation], evaluated_at: datetime) -> MacroSignal | None:
        latest = self._latest(observations)
        if latest is None:
            return None
        prior_value = observations[-2].value if len(observations) >= 2 else latest.value
        delta = latest.value - prior_value
        trend = _map_employment(latest.value, self.config)
        confidence = self.config.base_evidence_confidence
        return MacroSignal(
            signal_id=build_signal_id(self.category, latest.series_id, evaluated_at),
            category=self.category,
            label=f"employment {trend.value.lower()} (unemployment={latest.value:.2f}{latest.units})",
            trend_enum=trend.value,
            latest_value=latest.value,
            latest_units=latest.units,
            delta=delta,
            observed_at=latest.observed_at,
            available_at=latest.available_at,
            source=latest.source,
            source_fingerprint=latest.source_fingerprint,
            confidence=confidence,
            metadata={"series_id": latest.series_id},
        )

    def signal_id(self, observations: Sequence[MacroObservation], evaluated_at: datetime) -> str | None:
        latest = self._latest(observations)
        if latest is None:
            return None
        return build_signal_id(self.category, latest.series_id, evaluated_at)
