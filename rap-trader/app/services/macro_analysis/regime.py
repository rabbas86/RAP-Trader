"""Deterministic macro-regime classification from collected signals.

``MacroRegimeService`` fuses the individual trend enums from the specialist
services into a single ``MacroRegime``.  The classification is deterministic:
conflicting or missing evidence drives the regime toward ``UNKNOWN`` or
``STAGFLATION`` rather than a strong directional call.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.macro_analysis.base import MacroSignal
from app.services.macro_analysis.config import MacroAnalystConfig
from app.services.macro_analysis.domain import (
    BusinessCyclePhase,
    CreditCondition,
    EmploymentTrend,
    GrowthTrend,
    InflationTrend,
    LiquidityTrend,
    MacroRegime,
    PolicyStance,
    YieldCurveTrend,
)

# Ordered mapping from evidence category to the set of categories that count
# toward "sufficient evidence" for regime classification.
MACRO_EVIDENCE_CATEGORIES: frozenset[str] = frozenset(
    {
        "inflation",
        "growth",
        "employment",
        "liquidity",
        "monetary_policy",
        "yield_curve",
        "credit",
        "business_cycle",
    }
)


@dataclass(frozen=True)
class RegimeSignals:
    """Bundle of trend enums extracted from macro signals."""

    inflation: InflationTrend = InflationTrend.UNKNOWN
    growth: GrowthTrend = GrowthTrend.UNKNOWN
    employment: EmploymentTrend = EmploymentTrend.UNKNOWN
    liquidity: LiquidityTrend = LiquidityTrend.UNKNOWN
    policy: PolicyStance = PolicyStance.UNKNOWN
    yield_curve: YieldCurveTrend = YieldCurveTrend.UNKNOWN
    credit: CreditCondition = CreditCondition.UNKNOWN
    business_cycle: BusinessCyclePhase = BusinessCyclePhase.UNKNOWN
    signal_count: int = 0
    missing_categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class RegimeResult:
    regime: MacroRegime
    confidence: float
    signals: RegimeSignals
    rationale: str = ""


def _find_trend(signals: list[MacroSignal], category: str) -> str | None:
    """Return the trend_enum string for the first signal matching ``category``."""
    for signal in signals:
        if signal.category == category:
            return signal.trend_enum
    return None


def _safe_trend(category: str, signals: list[MacroSignal]) -> str:
    """Return the trend enum value string for ``category``, defaulting to UNKNOWN."""
    raw = _find_trend(signals, category)
    if raw is None:
        return "UNKNOWN"
    return raw


class MacroRegimeService:
    """Classify the overall macro regime from trend signals."""

    def __init__(self, config: MacroAnalystConfig | None = None) -> None:
        self.config = config or MacroAnalystConfig()

    def classify(self, signals: list[MacroSignal]) -> RegimeResult:
        # Build the typed signal bundle from trend-enum value strings.
        inflation_enum = InflationTrend(_safe_trend("inflation", signals))
        growth_enum = GrowthTrend(_safe_trend("growth", signals))
        employment_enum = EmploymentTrend(_safe_trend("employment", signals))
        liquidity_enum = LiquidityTrend(_safe_trend("liquidity", signals))
        policy_enum = PolicyStance(_safe_trend("monetary_policy", signals))
        yield_curve_enum = YieldCurveTrend(_safe_trend("yield_curve", signals))
        credit_enum = CreditCondition(_safe_trend("credit", signals))
        business_cycle_enum = BusinessCyclePhase(_safe_trend("business_cycle", signals))

        present = {signal.category for signal in signals}
        missing = tuple(sorted(MACRO_EVIDENCE_CATEGORIES - present))
        signal_count = len(signals)
        has_enough = signal_count >= self.config.min_regime_categories
        has_growth = growth_enum is not GrowthTrend.UNKNOWN
        has_inflation = inflation_enum is not InflationTrend.UNKNOWN

        bundle = RegimeSignals(
            inflation=inflation_enum,
            growth=growth_enum,
            employment=employment_enum,
            liquidity=liquidity_enum,
            policy=policy_enum,
            yield_curve=yield_curve_enum,
            credit=credit_enum,
            business_cycle=business_cycle_enum,
            signal_count=signal_count,
            missing_categories=missing,
        )

        # --- Priority-ordered regime rules ---------------------------------
        # 1. Crisis regimes (highest priority, require sufficient evidence).
        # Stagflation: accelerating inflation with negative growth.
        if has_enough and inflation_enum is InflationTrend.ACCELERATING and growth_enum is GrowthTrend.NEGATIVE:
            return RegimeResult(
                MacroRegime.STAGFLATION,
                0.75,
                bundle,
                "accelerating inflation with negative growth signals stagflation",
            )

        # Inflation shock: inflation accelerating, growth not yet negative.
        if has_enough and inflation_enum is InflationTrend.ACCELERATING:
            return RegimeResult(
                MacroRegime.INFLATION_SHOCK,
                0.65,
                bundle,
                "inflation is accelerating rapidly",
            )

        # Deflation risk: decelerating inflation with negative growth.
        if has_enough and inflation_enum is InflationTrend.DECELERATING and growth_enum is GrowthTrend.NEGATIVE:
            return RegimeResult(
                MacroRegime.DEFLATION_RISK,
                0.65,
                bundle,
                "decelerating inflation with negative growth signals deflation risk",
            )

        # 2. Growth + inflation driven regimes (require both signals).
        # These take priority over liquidity/policy regimes so that a clear
        # growth signal is not masked by a secondary liquidity or policy reading.
        if has_growth and has_inflation and has_enough:
            regime, confidence, rationale = _growth_inflation_regime(growth_enum, inflation_enum, credit_enum, yield_curve_enum, bundle)
            return RegimeResult(regime, confidence, bundle, rationale)

        # 3. Liquidity-driven regimes — only when growth signal is absent.
        if growth_enum is GrowthTrend.UNKNOWN and liquidity_enum is LiquidityTrend.EXPANDING:
            confidence = 0.45
            return RegimeResult(MacroRegime.LIQUIDITY_EXPANSION, confidence, bundle, "broad liquidity is expanding; growth signal absent")

        if growth_enum is GrowthTrend.UNKNOWN and liquidity_enum is LiquidityTrend.CONTRACTING:
            confidence = 0.45
            return RegimeResult(
                MacroRegime.LIQUIDITY_CONTRACTION, confidence, bundle, "broad liquidity is contracting; growth signal absent"
            )

        # 4. Policy-driven regimes — only when growth signal is absent.
        if growth_enum is GrowthTrend.UNKNOWN and policy_enum is PolicyStance.RESTRICTIVE:
            confidence = 0.4
            return RegimeResult(MacroRegime.TIGHTENING, confidence, bundle, "monetary policy is restrictive; growth signal absent")

        if growth_enum is GrowthTrend.UNKNOWN and policy_enum is PolicyStance.ACCOMMODATIVE:
            confidence = 0.4
            return RegimeResult(MacroRegime.EASING, confidence, bundle, "monetary policy is accommodative; growth signal absent")

        # 5. Insufficient evidence.
        if not has_enough:
            return RegimeResult(
                MacroRegime.UNKNOWN,
                0.2,
                bundle,
                "insufficient macro evidence to classify a regime",
            )

        # 6. Fallback: use business-cycle phase when we have some signal.
        if business_cycle_enum is BusinessCyclePhase.EXPANSION:
            return RegimeResult(MacroRegime.EXPANSION, 0.5, bundle, "expansion phase indicated by growth and employment data")
        if business_cycle_enum is BusinessCyclePhase.CONTRACTION:
            return RegimeResult(
                MacroRegime.RECESSION, 0.5, bundle, "contraction phase indicated by negative growth and weakening employment"
            )
        if business_cycle_enum is BusinessCyclePhase.PEAK:
            return RegimeResult(MacroRegime.PEAK, 0.5, bundle, "peak phase indicated by decelerating growth")

        return RegimeResult(
            MacroRegime.UNKNOWN,
            0.3,
            bundle,
            "weak or conflicting evidence; regime is indeterminate",
        )


def _growth_inflation_regime(
    growth: GrowthTrend,
    inflation: InflationTrend,
    credit: CreditCondition,
    yield_curve: YieldCurveTrend,
    bundle: RegimeSignals,
) -> tuple[MacroRegime, float, str]:
    """Map growth vs. inflation trends to an expansion/contraction family regime."""
    if growth is GrowthTrend.NEGATIVE:
        if inflation is InflationTrend.ACCELERATING:
            return MacroRegime.STAGFLATION, 0.75, "negative growth with accelerating inflation -> stagflation"
        return MacroRegime.RECESSION, 0.6, "negative growth with stable/decelerating inflation -> recession"
    if growth is GrowthTrend.ACCELERATING:
        return MacroRegime.EXPANSION, 0.6, "growth accelerating -> expansion"
    if growth is GrowthTrend.DECELERATING:
        if yield_curve is YieldCurveTrend.INVERTED:
            return MacroRegime.PEAK, 0.55, "decelerating growth with inverted curve -> peak"
        return MacroRegime.SLOWDOWN, 0.55, "growth decelerating -> slowdown"
    if growth is GrowthTrend.STABLE:
        return MacroRegime.SLOW_EXPANSION, 0.5, "stable growth with stable inflation -> slow expansion"
    return MacroRegime.UNKNOWN, 0.35, "indeterminate growth regime"
