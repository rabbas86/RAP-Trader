"""Deterministic trend helpers shared across macro specialist services.

Every service reduces a sorted series of ``MacroObservation`` values to a trend
enum using the same deterministic logic so the regime classification is
reproducible and auditable.
"""

from collections.abc import Sequence

from app.services.macro_analysis.config import MacroAnalystConfig
from app.services.macro_analysis.domain import (
    BusinessCyclePhase,
    CreditCondition,
    EmploymentTrend,
    GrowthTrend,
    InflationTrend,
    LiquidityTrend,
    PolicyStance,
    YieldCurveTrend,
)
from app.services.macro_analysis.observations import MacroObservation


def latest_observation(observations: Sequence[MacroObservation]) -> MacroObservation | None:
    """Return the most recent observation, or None when the series is empty."""
    return observations[-1] if observations else None


def _delta_percent(latest: float, prior: float) -> float:
    """Percentage-point change when values are rates/levels, or percent change
    for index-style values.  The sign is what matters; the magnitude is used
    only for threshold classification.
    """
    if prior == 0:
        return 1.0 if latest > 0 else 0.0
    return latest - prior


def _slope(observations: Sequence[MacroObservation]) -> float | None:
    """Simple two-point slope (latest - prior) for the most recent pair."""
    if len(observations) < 2:
        return None
    return observations[-1].value - observations[-2].value


def _classify_trend(
    observations: Sequence[MacroObservation],
    accel_threshold: float,
    decel_threshold: float,
    *,
    negative_trend: bool = False,
) -> tuple[str | None, float, float | None, float | None]:
    """Return (direction_label, latest_value, delta, prior_value) or (None, ...)."""
    latest = latest_observation(observations)
    if latest is None:
        return None, 0.0, 0.0, None
    prior_value: float | None = None
    delta: float = 0.0
    if len(observations) >= 2:
        prior_value = observations[-2].value
        delta = _delta_percent(latest.value, prior_value)
    direction = _slope_direction(delta, accel_threshold, decel_threshold, negative_trend=negative_trend)
    return direction, latest.value, delta, prior_value


def _slope_direction(
    delta: float,
    accel_threshold: float,
    decel_threshold: float,
    *,
    negative_trend: bool = False,
) -> str:
    if negative_trend:
        # For metrics where a *decline* is bad (e.g. money supply).
        if delta < -decel_threshold:
            return "declining"
        if delta > accel_threshold:
            return "rising"
        return "stable"
    if delta > accel_threshold:
        return "rising"
    if delta < -decel_threshold:
        return "falling"
    return "stable"


def _map_inflation(delta: float, latest: float, high_warning: float, config: MacroAnalystConfig) -> InflationTrend:
    if latest >= high_warning:
        if delta > config.inflation_stability_threshold:
            return InflationTrend.ACCELERATING
        if delta < -config.inflation_stability_threshold:
            return InflationTrend.DECELERATING
        return InflationTrend.STABLE
    if delta > config.inflation_stability_threshold:
        return InflationTrend.ACCELERATING
    if delta < -config.inflation_stability_threshold:
        return InflationTrend.DECELERATING
    return InflationTrend.STABLE


def _map_growth(delta: float, latest: float, config: MacroAnalystConfig) -> GrowthTrend:
    if latest < config.growth_negative_threshold:
        return GrowthTrend.NEGATIVE
    if delta > config.growth_accelerating_threshold:
        return GrowthTrend.ACCELERATING
    if delta < -config.growth_decelerating_threshold:
        return GrowthTrend.DECELERATING
    return GrowthTrend.STABLE


def _map_employment(latest: float, config: MacroAnalystConfig) -> EmploymentTrend:
    if latest < config.unemployment_low_threshold:
        return EmploymentTrend.STRENGTHENING
    if latest > config.unemployment_high_threshold:
        return EmploymentTrend.WEAKENING
    return EmploymentTrend.STABLE


def _map_liquidity(delta: float, config: MacroAnalystConfig) -> LiquidityTrend:
    if delta < -config.money_supply_contraction_threshold:
        return LiquidityTrend.CONTRACTING
    if delta > config.money_supply_contraction_threshold:
        return LiquidityTrend.EXPANDING
    return LiquidityTrend.STABLE


def _map_policy_rate(latest: float, delta: float, config: MacroAnalystConfig) -> PolicyStance:
    if latest >= config.policy_rate_high_threshold:
        return PolicyStance.RESTRICTIVE
    if latest <= config.policy_rate_low_threshold:
        return PolicyStance.ACCOMMODATIVE
    # Intermediate zone: direction matters for transition classification.
    if delta < 0:
        return PolicyStance.NEUTRAL
    if delta > 0:
        return PolicyStance.NEUTRAL
    return PolicyStance.NEUTRAL


def _map_yield_curve(delta: float, latest: float, config: MacroAnalystConfig) -> YieldCurveTrend:
    if latest < config.yield_curve_inversion_threshold:
        return YieldCurveTrend.INVERTED
    if delta > config.yield_curve_inversion_threshold:
        return YieldCurveTrend.STEEPENING
    return YieldCurveTrend.NORMAL


def _map_credit(latest: float, config: MacroAnalystConfig) -> CreditCondition:
    if latest > config.credit_spread_tightening_threshold:
        return CreditCondition.TIGHTENING
    if latest < config.credit_spread_tightening_threshold * 0.5:
        return CreditCondition.LOOSENING
    return CreditCondition.STABLE


def _map_business_cycle(
    growth: GrowthTrend,
    employment: EmploymentTrend,
    inflation: InflationTrend,
) -> BusinessCyclePhase:
    if growth is GrowthTrend.NEGATIVE:
        return BusinessCyclePhase.CONTRACTION
    if growth is GrowthTrend.ACCELERATING and employment is not EmploymentTrend.WEAKENING:
        return BusinessCyclePhase.EXPANSION
    if growth is GrowthTrend.DECELERATING:
        return BusinessCyclePhase.PEAK
    return BusinessCyclePhase.UNKNOWN
