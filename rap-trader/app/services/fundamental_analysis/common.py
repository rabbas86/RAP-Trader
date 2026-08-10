"""Shared helpers for fundamental analysis services."""

from __future__ import annotations

from datetime import datetime

from app.domain.models.fundamental import FundamentalMetric


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    """Safe ratio: returns None when numerator, denominator, or both are None or denominator is zero."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def growth(current: float | None, prior: float | None) -> tuple[float | None, list[str]]:
    """Year-over-year growth rate with warnings for suppressed edge cases."""
    warnings: list[str] = []
    if current is None or prior is None:
        return None, warnings
    if prior == 0:
        warnings.append("suppressed growth: zero base")
        return None, warnings
    if prior < 0:
        warnings.append("suppressed growth: negative base")
        return None, warnings
    return current / prior - 1.0, warnings


def metric(
    name: str,
    category: str,
    value: float | None,
    period_end: datetime | None,
    available_at: datetime,
    *,
    warnings: list[str] | None = None,
    assumptions: list[str] | None = None,
    units: str = "ratio",
) -> FundamentalMetric | None:
    """Create a FundamentalMetric, returning None when value is None."""
    if value is None:
        return None
    return FundamentalMetric(
        metric_id=f"{category}:{name}",
        name=name,
        category=category,
        value=value,
        units=units,
        period_end=period_end,
        available_at=available_at,
        source_fingerprint=f"deterministic:{name}",
        formula_version="1.0",
        valid=True,
        warnings=list(warnings) if warnings else [],
        assumptions=list(assumptions) if assumptions else [],
    )
