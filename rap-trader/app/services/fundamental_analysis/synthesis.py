"""Deterministic synthesis of fundamental evidence into an analyst opinion."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from app.domain.models.analyst import AnalysisDirection, EvidenceItem


@dataclass(frozen=True)
class SynthesisResult:
    direction: AnalysisDirection
    confidence: float
    stale_fraction: float
    conflict_fraction: float


# Category weights: business quality (50%), earnings/shareholder quality (30%), valuation (20%)
_CATEGORY_WEIGHTS: dict[str, float] = {
    "growth": 0.10,
    "profitability": 0.10,
    "cash_flow": 0.10,
    "balance_sheet": 0.10,
    "capital_efficiency": 0.10,
    "earnings_quality": 0.08,
    "shareholder": 0.07,
    "valuation": 0.05,
    "data_quality": 0.0,
}

_STRENGTH_WEIGHT: dict[str, float] = {"STRONG": 1.0, "MODERATE": 0.7, "WEAK": 0.4, "SPECULATIVE": 0.2}

_NEGATIVE_TERMS = {"decline", "deterioration", "negative", "weak", "suppressed", "violation", "impossible", "low"}

# Ratios above these limits are treated as warnings. Keeping the limits in one
# table makes the policy explicit and allows deployments to tune it if needed.
_HIGH_IS_NEGATIVE_THRESHOLDS: dict[str, float] = {
    "debt_to_equity": 2.0,
    "debt_to_assets": 0.60,
    "accrual_intensity": 0.10,
    "pe": 30.0,
    "forward_pe": 30.0,
    "pb": 5.0,
    "ps": 10.0,
    "ev_ebitda": 20.0,
    "net_debt_to_ebitda": 3.0,
    "financing_dependence": 0.50,
}

_METRIC_VALUE_PATTERN = re.compile(
    r"^\s*[^:]+:\s*(?P<metric>[A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*"
    r"(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
)


def _metric_value_is_negative(summary: str) -> bool:
    match = _METRIC_VALUE_PATTERN.match(summary)
    if match is None:
        return False

    metric_name = match.group("metric").lower()
    value = float(match.group("value"))

    if metric_name in {"cfo_net_income", "cfo_net_income_quality"}:
        return value < 1.0
    if metric_name == "earnings_quality_rating":
        return value < 2.0
    if metric_name in {"share_dilution", "shares_outstanding_growth"}:
        return value > 0.0

    threshold = _HIGH_IS_NEGATIVE_THRESHOLDS.get(metric_name)
    return threshold is not None and value > threshold


class FundamentalOpinionSynthesisService:
    def synthesize(self, evidence: list[EvidenceItem], as_of: datetime) -> SynthesisResult:
        if not evidence:
            return SynthesisResult(
                direction=AnalysisDirection.INSUFFICIENT_EVIDENCE,
                confidence=0.0,
                stale_fraction=0.0,
                conflict_fraction=0.0,
            )

        categories = {item.summary.split(":")[0] for item in evidence}
        if len(categories) < 3:
            return SynthesisResult(
                direction=AnalysisDirection.INSUFFICIENT_EVIDENCE,
                confidence=0.0,
                stale_fraction=0.0,
                conflict_fraction=0.0,
            )

        positive: float = 0.0
        negative: float = 0.0
        stale_count = 0
        total = len(evidence)

        for item in evidence:
            category = item.summary.split(":")[0]
            weight = _CATEGORY_WEIGHTS.get(category, 0.0)
            strength_str = item.strength.value if hasattr(item.strength, "value") else str(item.strength)
            strength_weight = _STRENGTH_WEIGHT.get(strength_str, 0.5)
            contribution = weight * strength_weight * item.confidence

            summary_lower = item.summary.lower()
            if any(term in summary_lower for term in _NEGATIVE_TERMS) or _metric_value_is_negative(item.summary):
                negative += contribution
            else:
                positive += contribution

            # Staleness: use 120-day threshold for financial statement evidence
            age = (as_of - item.available_at).days
            if age > 120:
                stale_count += 1

        total_score = positive + negative
        if total_score > 0:
            conflict_fraction = min(positive, negative) / total_score
        else:
            conflict_fraction = 0.0

        stale_fraction = stale_count / total if total > 0 else 0.0

        if conflict_fraction >= 0.30:
            direction = AnalysisDirection.MIXED
        elif total_score > 0.30:
            direction = AnalysisDirection.BULLISH
        elif total_score <= -0.30:
            direction = AnalysisDirection.BEARISH
        else:
            direction = AnalysisDirection.NEUTRAL

        # Confidence: uncalibrated, capped at 0.65
        coverage = min(1.0, len(categories) / 8)
        confidence = (0.35 + 0.25 * coverage + 0.2 * (1 - conflict_fraction)) * (1 - 0.5 * stale_fraction)
        confidence = min(confidence, 0.65)

        return SynthesisResult(
            direction=direction,
            confidence=confidence,
            stale_fraction=stale_fraction,
            conflict_fraction=conflict_fraction,
        )
