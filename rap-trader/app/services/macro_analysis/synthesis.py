"""Deterministic synthesis of macro signals into an analyst opinion.

``MacroOpinionSynthesisService`` turns classified macro signals into a
Phase-5 ``AnalysisDirection`` and a synthesis confidence score.  The
synthesis is deterministic: conflicting evidence reduces both the confidence
and the decisiveness of the direction.

The direction semantics for a macro regime are:
* BULLISH  — evidence points to healthy growth / easing / recovery
* BEARISH  — evidence points to contraction / tightening / recession
* NEUTRAL  — mixed or indeterminate evidence, or a regime that is neither
* MIXED    — strongly conflicting evidence across categories
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.services.macro_analysis.base import MacroSignal
from app.services.macro_analysis.config import MacroAnalystConfig
from app.services.macro_analysis.domain import (
    MacroRegime,
)
from app.services.macro_analysis.regime import MACRO_EVIDENCE_CATEGORIES


@dataclass(frozen=True)
class SynthesisResult:
    direction: str
    confidence: float
    stale_fraction: float
    conflict_fraction: float
    regime: MacroRegime
    signal_count: int
    missing_categories: tuple[str, ...]


class MacroOpinionSynthesisService:
    """Fuse macro signals into a deterministic opinion direction + confidence."""

    def __init__(self, config: MacroAnalystConfig | None = None) -> None:
        self.config = config or MacroAnalystConfig()

    def synthesize(
        self,
        signals: list[MacroSignal],
        as_of: datetime,
        regime: MacroRegime,
    ) -> SynthesisResult:
        if not signals:
            return SynthesisResult(
                direction="INSUFFICIENT_EVIDENCE",
                confidence=0.0,
                stale_fraction=0.0,
                conflict_fraction=0.0,
                regime=MacroRegime.UNKNOWN,
                signal_count=0,
                missing_categories=tuple(sorted(MACRO_EVIDENCE_CATEGORIES)),
            )

        # Count stale signals (observed more than stale_threshold before as_of).
        stale_count = sum(1 for signal in signals if (as_of - signal.observed_at).days > self.config.stale_threshold.days)
        total = len(signals)
        stale_fraction = stale_count / total if total > 0 else 0.0

        positive, negative = self._score_regime(regime, signals)

        total_score = positive + negative
        # Also compute signal-level conflict: are there both positive-trend
        # and negative-trend signals regardless of regime?
        signal_conflict = self._signal_level_conflict(signals)
        conflict_fraction = max(
            min(positive, negative) / total_score if total_score > 0 else 0.0,
            signal_conflict,
        )

        direction = self._classify_direction(regime, positive, negative, total_score, conflict_fraction)

        # Confidence: base on signal quality and regime clarity.
        coverage = min(1.0, total / self.config.min_regime_categories)
        conflict_penalty = 1.0 - 0.5 * conflict_fraction
        stale_penalty = 1.0 - 0.5 * stale_fraction
        confidence = 0.35 + 0.25 * coverage + 0.2 * (1.0 - conflict_fraction)
        confidence = min(confidence * conflict_penalty * stale_penalty, self.config.uncalibrated_confidence_cap)

        missing = tuple(sorted(MACRO_EVIDENCE_CATEGORIES - {signal.category for signal in signals}))

        return SynthesisResult(
            direction=direction,
            confidence=round(confidence, 6),
            stale_fraction=round(stale_fraction, 6),
            conflict_fraction=round(conflict_fraction, 6),
            regime=regime,
            signal_count=total,
            missing_categories=missing,
        )

    def _score_regime(self, regime: MacroRegime, signals: list[MacroSignal]) -> tuple[float, float]:
        """Return (positive_score, negative_score) for the regime.

        Scores are weighted sums of signal confidences.  Bullish regimes get
        positive weight; bearish regimes get negative weight.  Neutral or
        mixed regimes produce balanced scores.
        """
        bullish_regimes = {
            MacroRegime.EXPANSION,
            MacroRegime.RECOVERY,
            MacroRegime.EASING,
            MacroRegime.LIQUIDITY_EXPANSION,
            MacroRegime.SLOW_EXPANSION,
        }
        bearish_regimes = {
            MacroRegime.RECESSION,
            MacroRegime.TIGHTENING,
            MacroRegime.LIQUIDITY_CONTRACTION,
            MacroRegime.DEFLATION_RISK,
        }
        stagflationary = {MacroRegime.STAGFLATION, MacroRegime.INFLATION_SHOCK}

        positive: float = 0.0
        negative: float = 0.0

        if regime in bullish_regimes:
            positive = sum(signal.confidence for signal in signals)
        elif regime in bearish_regimes:
            negative = sum(signal.confidence for signal in signals)
        elif regime in stagflationary:
            # Stagflation: both growth and inflation are bad → mixed with
            # a slight negative lean.
            positive = sum(signal.confidence for signal in signals) * 0.3
            negative = sum(signal.confidence for signal in signals) * 0.7
        else:
            # NEUTRAL, PEAK, SLOWDOWN, UNKNOWN — split based on signal trends.
            for signal in signals:
                if signal.trend_enum in {
                    "ACCELERATING",
                    "STRENGTHENING",
                    "EXPANDING",
                    "STEEPENING",
                    "LOOSENING",
                    "ACCOMMODATIVE",
                    "NORMAL",
                    "STABLE",
                    "RECOVERY",
                    "EXPANSION",
                }:
                    positive += signal.confidence
                elif signal.trend_enum in {
                    "DECELERATING",
                    "WEAKENING",
                    "CONTRACTING",
                    "INVERTED",
                    "TIGHTENING",
                    "RESTRICTIVE",
                    "NEGATIVE",
                    "CONTRACTION",
                    "PEAK",
                }:
                    negative += signal.confidence
            # If balanced, the default NEUTRAL path keeps conflict_fraction high.

        return positive, negative

    def _signal_level_conflict(self, signals: list[MacroSignal]) -> float:
        """Fraction of total signal confidence that is 'negative' when both directions exist.

        This catches conflicts that the regime-level scoring might miss — for
        example, accelerating growth alongside decelerating inflation where the
        regime classifies as EXPANSION (bullish) but a specific category (inflation)
        is still showing a downward trend.
        """
        positive_signals = [
            s
            for s in signals
            if s.trend_enum
            in {
                "ACCELERATING",
                "STRENGTHENING",
                "EXPANDING",
                "STEEPENING",
                "LOOSENING",
                "ACCOMMODATIVE",
                "NORMAL",
                "STABLE",
                "RECOVERY",
                "EXPANSION",
            }
        ]
        negative_signals = [
            s
            for s in signals
            if s.trend_enum
            in {"DECELERATING", "WEAKENING", "CONTRACTING", "INVERTED", "TIGHTENING", "RESTRICTIVE", "NEGATIVE", "CONTRACTION", "PEAK"}
        ]
        if not positive_signals or not negative_signals:
            return 0.0
        pos = sum(s.confidence for s in positive_signals)
        neg = sum(s.confidence for s in negative_signals)
        total = pos + neg
        if total == 0:
            return 0.0
        return min(pos, neg) / total

    def _classify_direction(
        self,
        regime: MacroRegime,
        positive: float,
        negative: float,
        total_score: float,
        conflict_fraction: float,
    ) -> str:
        """Map the regime and score balance to a Phase-5 direction string."""
        if total_score == 0:
            return "INSUFFICIENT_EVIDENCE"

        if regime in {
            MacroRegime.STAGFLATION,
            MacroRegime.INFLATION_SHOCK,
            MacroRegime.DEFLATION_RISK,
            MacroRegime.RECESSION,
            MacroRegime.TIGHTENING,
            MacroRegime.LIQUIDITY_CONTRACTION,
        }:
            if conflict_fraction >= 0.30:
                return "MIXED"
            return "BEARISH"

        if regime in {
            MacroRegime.EXPANSION,
            MacroRegime.RECOVERY,
            MacroRegime.EASING,
            MacroRegime.LIQUIDITY_EXPANSION,
            MacroRegime.SLOW_EXPANSION,
        }:
            if conflict_fraction >= 0.30:
                return "MIXED"
            return "BULLISH"

        # NEUTRAL / PEAK / SLOWDOWN / UNKNOWN
        if conflict_fraction >= 0.30:
            return "MIXED"
        if total_score > 0.15:
            return "NEUTRAL"
        return "INSUFFICIENT_EVIDENCE"
