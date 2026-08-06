"""Deterministic evidence-to-direction synthesis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from app.domain.models.analyst import AnalysisDirection, EvidenceItem, EvidenceStrength


@dataclass(frozen=True)
class SynthesisResult:
    direction: AnalysisDirection
    confidence: float
    stale_fraction: float
    conflict_fraction: float
    calibrated: bool


class TechnicalEvidenceSynthesizer:
    _WEIGHTS: ClassVar[dict[EvidenceStrength, float]] = {
        EvidenceStrength.STRONG: 1.0,
        EvidenceStrength.MODERATE: 0.7,
        EvidenceStrength.WEAK: 0.4,
        EvidenceStrength.SPECULATIVE: 0.2,
    }
    _CATEGORIES: ClassVar[frozenset[str]] = frozenset({"trend", "momentum", "volatility", "volume", "structure", "levels"})

    @staticmethod
    def _orientation(summary: str) -> int:
        text = summary.lower()
        positive = sum(word in text for word in ("bullish", "uptrend", "above", "positive", "support holding"))
        negative = sum(word in text for word in ("bearish", "downtrend", "below", "negative", "resistance holding"))
        return 1 if positive > negative else -1 if negative > positive else 0

    def synthesize(self, evidence: list[EvidenceItem], as_of: datetime) -> SynthesisResult:
        if not evidence:
            return SynthesisResult(AnalysisDirection.INSUFFICIENT_EVIDENCE, 0.0, 0.0, 0.0, False)
        votes = [(self._orientation(item.summary), self._WEIGHTS[item.strength] * item.confidence) for item in evidence]
        directional = [(vote, weight) for vote, weight in votes if vote]
        categories = {item.summary.split(":", 1)[0].lower() for item in evidence}
        coverage = len(categories & self._CATEGORIES) / len(self._CATEGORIES)
        stale_fraction = sum(item.valid_until < as_of for item in evidence) / len(evidence)
        calibrated = any(item.has_historical_calibration for item in evidence)
        if not directional or coverage < 0.34:
            return SynthesisResult(AnalysisDirection.INSUFFICIENT_EVIDENCE, 0.0, stale_fraction, 0.0, calibrated)
        positive = sum(weight for vote, weight in directional if vote > 0)
        negative = sum(weight for vote, weight in directional if vote < 0)
        total = positive + negative
        conflict = min(positive, negative) / total if total else 0.0
        net = (positive - negative) / total if total else 0.0
        direction = (
            AnalysisDirection.MIXED
            if conflict >= 0.3
            else AnalysisDirection.BULLISH
            if net > 0.12
            else AnalysisDirection.BEARISH
            if net < -0.12
            else AnalysisDirection.NEUTRAL
        )
        confidence = min(1.0, (0.35 + 0.45 * abs(net) + 0.2 * coverage) * (1 - 0.5 * stale_fraction) * (1 - 0.5 * conflict))
        if calibrated:
            confidence = min(1.0, confidence + 0.05)
        return SynthesisResult(direction, round(confidence, 6), stale_fraction, conflict, calibrated)
