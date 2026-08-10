"""Domain models for the Phase 8B Macro Economist.

These are deterministic, offline classification primitives that turn
``NormalizedDataRecord`` series from the Phase 8A Unified Research Data Platform
into discrete macro signals and a ``MacroRegime``.

No network, no LLM, no model download.  These models are research-only and
never produce trades.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from app.domain.models.analyst import EvidenceType


class MacroRegime(StrEnum):
    """Coarse macro-economic regime classification."""

    UNKNOWN = "UNKNOWN"
    EXPANSION = "EXPANSION"
    SLOW_EXPANSION = "SLOW_EXPANSION"
    PEAK = "PEAK"
    SLOWDOWN = "SLOWDOWN"
    RECESSION = "RECESSION"
    RECOVERY = "RECOVERY"
    TIGHTENING = "TIGHTENING"
    EASING = "EASING"
    STAGFLATION = "STAGFLATION"
    INFLATION_SHOCK = "INFLATION_SHOCK"
    DEFLATION_RISK = "DEFLATION_RISK"
    LIQUIDITY_EXPANSION = "LIQUIDITY_EXPANSION"
    LIQUIDITY_CONTRACTION = "LIQUIDITY_CONTRACTION"


class InflationTrend(StrEnum):
    """Direction of inflation pressure."""

    ACCELERATING = "ACCELERATING"
    DECELERATING = "DECELERATING"
    STABLE = "STABLE"
    UNKNOWN = "UNKNOWN"


class GrowthTrend(StrEnum):
    """Direction of real economic growth."""

    ACCELERATING = "ACCELERATING"
    DECELERATING = "DECELERATING"
    STABLE = "STABLE"
    NEGATIVE = "NEGATIVE"
    UNKNOWN = "UNKNOWN"


class EmploymentTrend(StrEnum):
    """Labour-market tightness."""

    STRENGTHENING = "STRENGTHENING"
    WEAKENING = "WEAKENING"
    STABLE = "STABLE"
    UNKNOWN = "UNKNOWN"


class LiquidityTrend(StrEnum):
    """Momentum of broad liquidity."""

    EXPANDING = "EXPANDING"
    CONTRACTING = "CONTRACTING"
    STABLE = "STABLE"
    UNKNOWN = "UNKNOWN"


class PolicyStance(StrEnum):
    """Monetary-policy posture."""

    RESTRICTIVE = "RESTRICTIVE"
    ACCOMMODATIVE = "ACCOMMODATIVE"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class YieldCurveTrend(StrEnum):
    """Yield-curve shape regime."""

    INVERTED = "INVERTED"
    NORMAL = "NORMAL"
    FLAT = "FLAT"
    STEEPENING = "STEEPENING"
    UNKNOWN = "UNKNOWN"


class CreditCondition(StrEnum):
    """Credit-conditions regime."""

    TIGHTENING = "TIGHTENING"
    LOOSENING = "LOOSENING"
    STABLE = "STABLE"
    UNKNOWN = "UNKNOWN"


class BusinessCyclePhase(StrEnum):
    """High-level business-cycle phase."""

    EXPANSION = "EXPANSION"
    PEAK = "PEAK"
    CONTRACTION = "CONTRACTION"
    TROUGH = "TROUGH"
    RECOVERY = "RECOVERY"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Evidence category labels (used as the prefix of EvidenceItem.summary so the
# framework's existing category-splitting logic works unchanged).
# ---------------------------------------------------------------------------
INFLATION_CATEGORY: Final[str] = "inflation"
GROWTH_CATEGORY: Final[str] = "growth"
EMPLOYMENT_CATEGORY: Final[str] = "employment"
LIQUIDITY_CATEGORY: Final[str] = "liquidity"
MONETARY_POLICY_CATEGORY: Final[str] = "monetary_policy"
YIELD_CURVE_CATEGORY: Final[str] = "yield_curve"
CREDIT_CATEGORY: Final[str] = "credit"
BUSINESS_CYCLE_CATEGORY: Final[str] = "business_cycle"
GLOBAL_RISK_CATEGORY: Final[str] = "global_risk"
DATA_QUALITY_CATEGORY: Final[str] = "data_quality"

# Evidence type assigned to all macro evidence items.
MACRO_EVIDENCE_TYPE: Final[EvidenceType] = EvidenceType.MACROECONOMIC
