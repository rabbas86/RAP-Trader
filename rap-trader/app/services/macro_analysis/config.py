"""Configuration for the Phase 8B Macro Economist.

Determines which data-platform records the Macro Economist will consider when
extracting observations, and what thresholds it applies when classifying the
macro regime. All thresholds are documented constants so that policy changes are
auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from app.domain.models.analyst import AnalystRole


@dataclass(frozen=True)
class MacroAnalystConfig:
    """Configuration for the deterministic, research-only macro economist."""

    analyst_id: str = "macro"
    role: AnalystRole = AnalystRole.MACRO
    research_only: bool = True
    suitable_for_live_trading: bool = False

    # --- Series the analyst knows how to interpret -------------------------
    # Keys are matched case-insensitively against a NormalizedDataRecord.series_id.
    series_whitelist: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "CPI",
                "CORE_CPI",
                "PCE",
                "CORE_PCE",
                "GDP",
                "GDP_TREND",
                "PMI",
                "INDUSTRIAL_PRODUCTION",
                "RETAIL_SALES",
                "POLICY_RATE",
                "YIELD_2Y",
                "YIELD_10Y",
                "YIELD_SPREAD",
                "CREDIT_SPREAD",
                "MONEY_SUPPLY",
                "UNEMPLOYMENT",
                "NONFARM_PAYROLLS",
                "HOUSING_STARTS",
                "CONSUMER_CONFIDENCE",
                "BUSINESS_SURVEY",
            }
        )
    )

    # --- Regime classification thresholds ---------------------------------
    # Inflation (percent, YoY)
    inflation_stability_threshold: float = 0.1  # pp change below this is "stable"
    inflation_accelerating_threshold: float = 3.0
    inflation_decelerating_threshold: float = 2.0
    inflation_high_warning: float = 5.0

    # Growth (percent, YoY)
    growth_accelerating_threshold: float = 2.0
    growth_decelerating_threshold: float = 1.0
    growth_negative_threshold: float = 0.0

    # Employment (percent)
    unemployment_low_threshold: float = 4.0
    unemployment_high_threshold: float = 6.0

    # Policy rate (percent)
    policy_rate_high_threshold: float = 5.0
    policy_rate_low_threshold: float = 2.0

    # Yield curve (10Y - 2Y, percent). Negative => inversion.
    yield_curve_inversion_threshold: float = 0.0

    # Credit spread (percent). Higher => tighter credit.
    credit_spread_tightening_threshold: float = 2.0

    # Money supply growth (percent, YoY). Negative => contraction.
    money_supply_contraction_threshold: float = 0.0

    # --- Evidence freshness and confidence --------------------------------
    stale_threshold: timedelta = field(default_factory=lambda: timedelta(days=7))
    # Confidence contributed per evidence item before framework capping.
    base_evidence_confidence: float = 0.7
    # Confidence floor for regime-classification evidence.
    regime_confidence_floor: float = 0.5
    # Minimum number of evidence categories required to render a regime.
    min_regime_categories: int = 4

    # --- Safety -----------------------------------------------------------
    uncalibrated_confidence_cap: float = 0.65
    stale_input_allowed: bool = False
