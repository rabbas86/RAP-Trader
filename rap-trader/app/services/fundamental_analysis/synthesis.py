"""Documented deterministic fundamental evidence synthesis."""

from dataclasses import dataclass
from datetime import datetime

from app.domain.models.analyst import AnalysisDirection, EvidenceItem, EvidenceStrength


@dataclass(frozen=True)
class FundamentalSynthesisResult:
    direction: AnalysisDirection
    confidence: float
    stale_fraction: float
    conflict_fraction: float


class FundamentalOpinionSynthesisService:
    """Require broad coverage; conflicts override score; valuation carries at most one category vote."""

    POSITIVE = ("growth", "margin", "coverage", "yield", "turnover", "quality")
    NEGATIVE = ("negative", "dilution", "debt-funded", "deteriorat", "weak")

    @classmethod
    def _vote(cls, item: EvidenceItem) -> int:
        text = item.summary.lower() + " " + " ".join(x.message.lower() for x in item.warnings)
        positive = sum(x in text for x in cls.POSITIVE)
        negative = sum(x in text for x in cls.NEGATIVE)
        return 1 if positive > negative else -1 if negative > positive else 0

    def synthesize(self, evidence: list[EvidenceItem], as_of: datetime) -> FundamentalSynthesisResult:
        categories = {x.summary.split(":", 1)[0] for x in evidence}
        if len(categories) < 3:
            return FundamentalSynthesisResult(AnalysisDirection.INSUFFICIENT_EVIDENCE, 0.0, 0.0, 0.0)
        weights = {
            EvidenceStrength.STRONG: 1.0,
            EvidenceStrength.MODERATE: 0.7,
            EvidenceStrength.WEAK: 0.4,
            EvidenceStrength.SPECULATIVE: 0.2,
        }
        votes = [(self._vote(x), weights[x.strength] * x.confidence) for x in evidence]
        positive = sum(w for v, w in votes if v > 0)
        negative = sum(w for v, w in votes if v < 0)
        total = positive + negative
        conflict = min(positive, negative) / total if total else 0.0
        stale = sum((as_of - x.available_at).days > (30 if x.evidence_type.value == "valuation" else 120) for x in evidence) / len(evidence)
        if total == 0:
            direction = AnalysisDirection.NEUTRAL
        elif conflict >= 0.3:
            direction = AnalysisDirection.MIXED
        else:
            net = (positive - negative) / total
            direction = AnalysisDirection.BULLISH if net > 0.15 else AnalysisDirection.BEARISH if net < -0.15 else AnalysisDirection.NEUTRAL
        coverage = min(1.0, len(categories) / 8)
        confidence = min(0.65, (0.35 + 0.25 * coverage + 0.2 * (1 - conflict)) * (1 - 0.5 * stale))
        return FundamentalSynthesisResult(direction, round(confidence, 6), stale, conflict)
