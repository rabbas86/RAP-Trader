"""Shared deterministic metric helpers."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from hashlib import sha256

from app.domain.models.fundamental import FundamentalMetric


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def metric(
    name: str,
    category: str,
    value: float | None,
    period_end: datetime,
    available_at: datetime,
    *,
    units: str = "ratio",
    warnings: Iterable[str] = (),
    assumptions: Iterable[str] = (),
) -> FundamentalMetric | None:
    if value is None:
        return None
    fingerprint = sha256(f"{name}|{value}|{period_end.isoformat()}|{available_at.isoformat()}".encode()).hexdigest()
    return FundamentalMetric(
        metric_id=f"{category}.{name}",
        name=name,
        category=category,
        value=round(value, 8),
        units=units,
        period_end=period_end,
        available_at=available_at,
        source_fingerprint=fingerprint,
        formula_version="1.0",
        valid=True,
        warnings=list(warnings),
        assumptions=list(assumptions),
    )


def growth(current: float, previous: float) -> tuple[float | None, str | None]:
    if previous == 0:
        return None, "growth unavailable because the comparison base is zero"
    if previous < 0:
        return None, "percentage growth suppressed because the comparison base is negative"
    return current / previous - 1, None
