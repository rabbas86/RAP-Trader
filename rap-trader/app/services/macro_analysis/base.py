"""Shared base class for deterministic macro analysis services.

Each specialist service (inflation, growth, employment, etc.) follows the same
contract: it consumes grouped observations, classifies a trend, and returns a
``MacroSignal``.  The base class centralises the deterministic opinion-id and
freshness logic so every service is auditable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.services.macro_analysis.observations import MacroObservation


@dataclass(frozen=True)
class MacroSignal:
    """A single classified macro signal with full provenance."""

    signal_id: str
    category: str  # evidence-item prefix (e.g. "inflation")
    label: str  # human-readable trend label
    trend_enum: str  # value of a trend enum (e.g. InflationTrend.ACCELERATING)
    latest_value: float
    latest_units: str
    delta: float
    observed_at: datetime
    available_at: datetime
    source: str
    source_fingerprint: str
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)


def build_signal_id(category: str, series_id: str, evaluated_at: datetime) -> str:
    return str(uuid5(NAMESPACE_URL, f"macro:{category}:{series_id}:{evaluated_at.isoformat()}"))


class MacroAnalysisService:
    """Base class for all deterministic macro analysis services."""

    category: str = "macro"

    def signal_ids(self, observations: Sequence[MacroObservation]) -> list[str]:
        """Return the deterministic signal ids that would be produced for a
        set of observations (without actually building the signals)."""
        raise NotImplementedError

    def classify(self, observations: Sequence[MacroObservation], evaluated_at: datetime) -> MacroSignal | None:
        """Classify observations into a signal.

        Subclasses implement the trend logic.  ``observed_at`` and
        ``available_at`` on the returned signal come from the latest observation,
        providing deterministic freshness for the evidence layer.
        """
        raise NotImplementedError

    @staticmethod
    def _latest(observations: Sequence[MacroObservation]) -> MacroObservation | None:
        from app.services.macro_analysis.trends import latest_observation

        return latest_observation(observations)
