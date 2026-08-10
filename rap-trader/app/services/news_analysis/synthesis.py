"""Deterministic synthesis of news events into an analyst opinion.

``NewsOpinionSynthesisService`` turns classified news clusters into a
Phase-5 ``AnalysisDirection`` and a synthesis confidence score.  The
synthesis is deterministic: conflicting evidence reduces both the confidence
and the decisiveness of the direction.

Direction semantics for news events:
* strongly_bullish           — evidence points to strongly positive material events
* bullish                    — net positive signal
* neutral                    — mixed or indeterminate evidence
* bearish                    — net negative signal
* strongly_bearish           — dominant strongly-negative high-importance events
* mixed                      — strongly conflicting evidence across categories
* insufficient_evidence      — too few events to render a direction

No BUY.  No SELL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.services.news_analysis.config import NewsAnalystConfig
from app.services.news_analysis.domain import (
    NewsImportance,
    NewsOrientation,
)


@dataclass(frozen=True)
class SynthesisResult:
    direction: str
    confidence: float
    stale_fraction: float
    conflict_fraction: float
    total_events: int
    positive_score: float
    negative_score: float
    duplicate_penalty: float
    unverified_penalty: float
    stale_penalty: float
    warnings: list[str] = field(default_factory=list)


# Importance → weight multiplier for scoring.
_IMPORTANCE_WEIGHTS: dict[NewsImportance, float] = {
    NewsImportance.CRITICAL: 4.0,
    NewsImportance.HIGH: 2.0,
    NewsImportance.MODERATE: 1.0,
    NewsImportance.LOW: 0.25,
    NewsImportance.UNKNOWN: 0.1,
    NewsImportance.TRIVIAL: 0.05,
}

# Orientation → signed multiplier (-2, -1, 0, +1, +2).
_ORIENTATION_SIGNS: dict[NewsOrientation, float] = {
    NewsOrientation.POSITIVE: 1.0,
    NewsOrientation.STRONGLY_POSITIVE: 2.0,
    NewsOrientation.NEUTRAL: 0.0,
    NewsOrientation.UNKNOWN: 0.0,
    NewsOrientation.NEGATIVE: -1.0,
    NewsOrientation.STRONGLY_NEGATIVE: -2.0,
    NewsOrientation.MIXED: 0.0,
}

# Threshold below which a negative-score ratio triggers strongly_bearish.
_STRONGLY_BEARISH_RATIO = 0.7
# Threshold above which a positive-score ratio triggers strongly_bullish.
_STRONGLY_BULLISH_RATIO = 0.7


class NewsOpinionSynthesisService:
    """Fuse classified news events into a deterministic opinion direction."""

    def __init__(self, config: NewsAnalystConfig | None = None) -> None:
        self.config = config or NewsAnalystConfig()

    def synthesize(
        self,
        classified: list[Any],
        as_of: datetime,
    ) -> SynthesisResult:
        """Synthesize classified events into a direction + confidence.

        ``classified`` is a list of objects with attributes:
        - ``orientation``: NewsOrientation
        - ``importance``: NewsImportance
        - ``decay_factor``: float (0.0-1.0)
        - ``is_duplicate``: bool
        - ``source_quality``: SourceQuality
        - ``confirmation_status``: ConfirmationStatus
        - ``is_excluded``: bool (True when lifecycle state is cancelled/superseded)
        - ``materiality_score``: float (0.0-1.0)
        - ``cluster_id``: str
        """
        if not classified:
            return _empty_result()

        warnings: list[str] = []

        # --- Compute positive/negative scores -------------------------------
        positive_score = 0.0
        negative_score = 0.0
        stale_count = 0
        duplicate_count = 0
        unverified_count = 0
        high_impact_count = 0
        excluded_count = 0
        conflict_detected = False

        from app.services.news_analysis.domain import (
            ConfirmationStatus,
            SourceQuality,
        )

        for item in classified:
            # Excluded events (cancelled / superseded) do not multiply weight.
            if getattr(item, "is_excluded", False):
                excluded_count += 1
                continue

            orientation = item.orientation
            importance = item.importance
            decay_factor = item.decay_factor
            is_duplicate = item.is_duplicate

            weight = _IMPORTANCE_WEIGHTS.get(importance, 0.1)
            sign = _ORIENTATION_SIGNS.get(orientation, 0.0)

            # Metric-aware negative signal detection.
            if _metric_value_is_negative(item):
                sign = min(sign, -1.0) if sign > 0 else sign
                weight = max(weight, 2.0)
                warnings.append(f"metric-aware negative signal detected for event {getattr(item, 'cluster_id', 'unknown')}")

            # Apply decay: stale events carry less weight.
            weighted = weight * decay_factor * getattr(item, "materiality_score", 1.0)
            if sign > 0:
                positive_score += weighted * sign
            elif sign < 0:
                negative_score += weighted * abs(sign)

            # Track metadata for confidence computation.
            stale_threshold = self.config.stale_decay_threshold
            if decay_factor < stale_threshold:
                stale_count += 1
            if is_duplicate:
                duplicate_count += 1
            if item.source_quality is SourceQuality.UNVERIFIED or item.confirmation_status.value == "unverified":
                unverified_count += 1
            if importance in (NewsImportance.CRITICAL, NewsImportance.HIGH):
                high_impact_count += 1

            # Detect cross-source contradictions.
            if item.confirmation_status is ConfirmationStatus.CONFLICTING:
                conflict_detected = True
                warnings.append(f"conflicting sources for event {getattr(item, 'cluster_id', 'unknown')}")

        if excluded_count > 0:
            warnings.append(f"{excluded_count} cancelled/superseded event(s) excluded from synthesis")

        total = len(classified)
        # Effective total is the count of non-excluded events.
        effective_total = total - excluded_count

        stale_fraction = stale_count / total if total > 0 else 0.0
        duplicate_penalty = duplicate_count / total if total > 0 else 0.0
        unverified_penalty = unverified_count / total if total > 0 else 0.0
        conflict_fraction = 1.0 if conflict_detected else 0.0

        if effective_total == 0:
            return _empty_result(warnings=warnings)

        direction = self._classify_direction(positive_score, negative_score, effective_total, conflict_fraction, warnings)
        confidence = self._compute_confidence(
            positive_score,
            negative_score,
            effective_total,
            stale_fraction,
            conflict_fraction,
            duplicate_penalty,
            unverified_penalty,
            high_impact_count,
        )

        if duplicate_count > 0:
            warnings.append(f"{duplicate_count} duplicate event(s) did not multiply evidence weight")
        if unverified_count > 0:
            warnings.append(f"{unverified_count} unverified event(s) received confidence penalty")
        if stale_count > 0:
            warnings.append(f"{stale_count} stale event(s) received decay penalty")
        if conflict_detected:
            warnings.append("conflicting primary sources produce strong uncertainty")

        return SynthesisResult(
            direction=direction,
            confidence=round(confidence, 6),
            stale_fraction=round(stale_fraction, 6),
            conflict_fraction=round(conflict_fraction, 6),
            total_events=total,
            positive_score=round(positive_score, 6),
            negative_score=round(negative_score, 6),
            duplicate_penalty=round(duplicate_penalty, 6),
            unverified_penalty=round(unverified_penalty, 6),
            stale_penalty=round(stale_fraction, 6),
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Direction classification
    # ------------------------------------------------------------------

    def _classify_direction(
        self,
        positive_score: float,
        negative_score: float,
        total: int,
        conflict_fraction: float,
        warnings: list[str],
    ) -> str:
        """Map scores to a direction string."""
        total_score = positive_score + negative_score

        if total == 0 or total_score == 0:
            return "insufficient_evidence"

        # Strong conflict with both directions present → MIXED.
        if conflict_fraction >= 0.3:
            warnings.append("conflict_fraction >= 0.3 triggers MIXED")
            if positive_score > 0 and negative_score > 0:
                return "mixed"

        ratio = abs(positive_score - negative_score) / total_score if total_score > 0 else 0.0

        if positive_score > 0 and negative_score > 0 and ratio < 0.5:
            # Both directions present but neither dominates strongly → MIXED.
            return "mixed"

        positive_ratio = positive_score / max(total_score, 1e-9)
        negative_ratio = negative_score / max(total_score, 1e-9)

        if positive_score > negative_score:
            if positive_ratio >= _STRONGLY_BULLISH_RATIO:
                return "strongly_bullish"
            return "bullish"
        if negative_score > positive_score:
            if negative_ratio >= _STRONGLY_BEARISH_RATIO:
                return "strongly_bearish"
            return "bearish"

        return "neutral"

    # ------------------------------------------------------------------
    # Confidence
    # ------------------------------------------------------------------

    def _compute_confidence(
        self,
        positive_score: float,
        negative_score: float,
        total: int,
        stale_fraction: float,
        conflict_fraction: float,
        duplicate_penalty: float,
        unverified_penalty: float,
        high_impact_count: int,
    ) -> float:
        """Compute a synthesis confidence in [0, uncalibrated_confidence_cap]."""
        if total == 0:
            return 0.0
        total_score = positive_score + negative_score
        # Coverage: how many effective events contributed signal.
        coverage = min(1.0, total / max(1.0, total))
        signal_strength = min(1.0, total_score / 6.0) if total_score > 0 else 0.0
        high_impact_factor = min(1.0, high_impact_count / 3.0)

        raw_confidence = 0.2 + 0.3 * signal_strength + 0.25 * coverage + 0.15 * high_impact_factor
        raw_confidence = min(raw_confidence, 1.0)

        # Penalties.
        stale_penalty = 1.0 - 0.5 * stale_fraction
        conflict_penalty = 1.0 - 0.5 * conflict_fraction
        dup_penalty = 1.0 - 0.3 * duplicate_penalty
        unverified_penalty_factor = 1.0 - 0.4 * unverified_penalty

        adjusted = raw_confidence * stale_penalty * conflict_penalty * dup_penalty * unverified_penalty_factor
        # Cap to the configured uncalibrated confidence cap.
        return min(adjusted, self.config.uncalibrated_confidence_cap)


def _empty_result(warnings: list[str] | None = None) -> SynthesisResult:
    return SynthesisResult(
        direction="insufficient_evidence",
        confidence=0.0,
        stale_fraction=0.0,
        conflict_fraction=0.0,
        total_events=0,
        positive_score=0.0,
        negative_score=0.0,
        duplicate_penalty=0.0,
        unverified_penalty=0.0,
        stale_penalty=0.0,
        warnings=warnings or ["no events to synthesize"],
    )


def _metric_value_is_negative(item: Any) -> bool:
    """Check actual metric values on the classified event for negative signals.

    This inspects the ``structured_payload`` of the underlying observation for
    key financial / operational metrics whose sign or magnitude indicates
    adverse movement, independent of keyword matching.  This prevents strong
    growth from automatically dominating the synthesis score.

    Metrics checked:
    - cash conversion quality: cfo_net_income < 1.0
    - leverage: debt_to_equity > 2.0, debt_to_assets > 0.60
    - dilution: share_dilution > 0
    - earnings quality: earnings_quality < 2.0
    - valuation: pe > 30, pb > 5, ev_revenue > 5, price_to_cash > 20
    """
    payload = getattr(item, "structured_payload", None)
    if payload is None:
        # Try reading from observation attribute.
        obs = getattr(item, "observation", None)
        if obs is not None:
            payload = getattr(obs, "structured_payload", None)
    if not isinstance(payload, dict):
        return False

    # Cash conversion quality.
    cfo_ni = _extract_numeric(payload.get("cfo_net_income"))
    if cfo_ni is not None and cfo_ni < 1.0:
        return True

    # Leverage ratios.
    dte = _extract_numeric(payload.get("debt_to_equity"))
    if dte is not None and dte > 2.0:
        return True

    dta = _extract_numeric(payload.get("debt_to_assets"))
    if dta is not None and dta > 0.60:
        return True

    # Share dilution.
    dil = _extract_numeric(payload.get("share_dilution"))
    if dil is not None and dil > 0:
        return True

    # Earnings quality.
    eq = _extract_numeric(payload.get("earnings_quality"))
    if eq is not None and eq < 2.0:
        return True

    # Valuation ratios — overvalued can be a negative signal.
    pe = _extract_numeric(payload.get("pe"))
    if pe is not None and pe > 30:
        return True

    pb = _extract_numeric(payload.get("pb"))
    if pb is not None and pb > 5:
        return True

    ev_rev = _extract_numeric(payload.get("ev_revenue"))
    if ev_rev is not None and ev_rev > 5:
        return True

    ptc = _extract_numeric(payload.get("price_to_cash"))
    return bool(ptc is not None and ptc > 20)


def _extract_numeric(*values: Any) -> float | None:
    for value in values:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, dict):
            inner = value.get("value")
            if isinstance(inner, (int, float)):
                return float(inner)
    return None
