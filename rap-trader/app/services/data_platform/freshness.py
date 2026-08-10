"""Deterministic freshness assessment."""

from __future__ import annotations

from datetime import datetime

from app.domain.models.market_data import _require_aware_utc


class ResearchDataFreshnessService:
    def age_seconds(self, available_at: datetime, as_of: datetime) -> float:
        available_at, as_of = _require_aware_utc(available_at), _require_aware_utc(as_of)
        if available_at > as_of:
            raise ValueError("available_at cannot be after as_of")
        return (as_of - available_at).total_seconds()

    def is_stale(self, available_at: datetime, as_of: datetime, stale_after_seconds: float) -> bool:
        if stale_after_seconds < 0:
            raise ValueError("stale_after_seconds must be non-negative")
        return self.age_seconds(available_at, as_of) > stale_after_seconds

    def timeliness_score(self, available_at: datetime, as_of: datetime, stale_after_seconds: float) -> float:
        if stale_after_seconds <= 0:
            return 1.0 if self.age_seconds(available_at, as_of) == 0 else 0.0
        return round(max(0.0, 1.0 - self.age_seconds(available_at, as_of) / stale_after_seconds), 6)


DataFreshnessService = ResearchDataFreshnessService
FreshnessService = ResearchDataFreshnessService
__all__ = ["DataFreshnessService", "FreshnessService", "ResearchDataFreshnessService"]
