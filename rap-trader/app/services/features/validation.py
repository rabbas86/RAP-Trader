"""Feature input and output validation."""

from __future__ import annotations

from datetime import datetime
from math import isfinite

from app.domain.models.features import FeatureError, FeatureErrorCode, FeatureScalar
from app.domain.models.market_data import OHLCVBar, _require_aware_utc


def validate_bars(bars: list[OHLCVBar], as_of: datetime, *, minimum: int = 1) -> list[OHLCVBar]:
    _require_aware_utc(as_of)
    causal = [bar for bar in bars if bar.timestamp <= as_of]
    if len(causal) < minimum:
        raise FeatureError(FeatureErrorCode.INSUFFICIENT_DATA, f"At least {minimum} causal bars are required")
    timestamps = [bar.timestamp for bar in causal]
    if timestamps != sorted(timestamps) or len(timestamps) != len(set(timestamps)):
        raise FeatureError(FeatureErrorCode.INVALID_REQUEST, "Bars must be sorted and unique")
    return causal


def validate_scalar(value: FeatureScalar) -> FeatureScalar:
    if isinstance(value, float) and not isfinite(value):
        raise FeatureError(FeatureErrorCode.COMPUTATION_FAILED, "A feature generator produced a non-finite value")
    return value
