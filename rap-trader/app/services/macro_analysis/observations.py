"""Extract typed macro observations from a ResearchDataSnapshot.

The Phase 8B Macro Economist consumes only ``ResearchDataSnapshot`` from the
Phase 8A Unified Research Data Platform.  This module is the single place where
snapshot records are projected into the scalar observations the specialist
services need.  Keeping the extraction logic in one service ensures that every
downstream service reads the same vetted values and that the snapshot is never
touched directly outside this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.models.data_platform import NormalizedDataRecord, ResearchDataSnapshot
from app.services.macro_analysis.config import MacroAnalystConfig


@dataclass(frozen=True)
class MacroObservation:
    """A single numeric macro reading with provenance."""

    series_id: str
    value: float
    units: str
    observed_at: datetime
    available_at: datetime
    source: str
    source_fingerprint: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ObservationExtractor:
    """Project ``NormalizedDataRecord`` rows into ``MacroObservation`` values."""

    def __init__(self, config: MacroAnalystConfig | None = None) -> None:
        self.config = config or MacroAnalystConfig()

    def _series_id(self, record: NormalizedDataRecord) -> str:
        series = record.series_id or ""
        return series.upper()

    def _value(self, record: NormalizedDataRecord) -> float | None:
        value = record.value
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, dict):
            extracted = value.get("value")
            if isinstance(extracted, (int, float)):
                return float(extracted)
        return None

    def _source(self, record: NormalizedDataRecord) -> str:
        return f"{record.source.provider}:{record.source.dataset}"

    def extract(self, snapshot: ResearchDataSnapshot) -> dict[str, list[MacroObservation]]:
        """Group records by normalized upper-cased series_id.

        Only records whose series_id is in the configured whitelist are
        returned; everything else is ignored so the analyst stays decoupled
        from irrelevant domains.
        """
        whitelist = {s.upper() for s in self.config.series_whitelist}
        grouped: dict[str, list[MacroObservation]] = {}
        for record in snapshot.records:
            series = self._series_id(record)
            if series not in whitelist:
                continue
            value = self._value(record)
            if value is None:
                continue
            observation = MacroObservation(
                series_id=series,
                value=value,
                units=str(record.units),
                observed_at=record.event_time or record.availability.observed_at,
                available_at=record.availability.available_at,
                source=self._source(record),
                source_fingerprint=record.source_fingerprint,
                metadata=dict(record.metadata),
            )
            grouped.setdefault(series, []).append(observation)

        # Sort each series chronologically (oldest first) so trend services
        # can compute deltas deterministically.
        for series, observations in grouped.items():
            grouped[series] = sorted(observations, key=lambda obs: obs.available_at)
        return grouped
