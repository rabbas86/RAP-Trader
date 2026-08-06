"""Timeframe-aware feature freshness assessment."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.domain.models.market_data import Timeframe, _require_aware_utc

STEPS: dict[Timeframe, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}


class FeatureFreshnessService:
    def __init__(self, allowed_intervals: float = 2.0) -> None:
        if allowed_intervals <= 0:
            raise ValueError("allowed_intervals must be positive")
        self.allowed_intervals = allowed_intervals

    def age_seconds(self, observed_at: datetime, as_of: datetime) -> float:
        observed, evaluated = _require_aware_utc(observed_at), _require_aware_utc(as_of)
        return max(0.0, (evaluated - observed).total_seconds())

    def is_stale(self, observed_at: datetime, as_of: datetime, timeframe: Timeframe) -> bool:
        return self.age_seconds(observed_at, as_of) > STEPS[timeframe].total_seconds() * self.allowed_intervals
