"""Deterministic analyst contribution mapping."""

from collections import defaultdict
from statistics import fmean, pstdev

from app.domain.models.analyst import AnalysisDirection, AnalystOpinion
from app.domain.models.portfolio import AnalystContribution
from app.services.portfolio.config import PortfolioManagerConfig

ORIENTATION = {
    AnalysisDirection.BULLISH: 1.0,
    AnalysisDirection.BEARISH: -1.0,
    AnalysisDirection.NEUTRAL: 0.0,
    AnalysisDirection.MIXED: 0.0,
    AnalysisDirection.INSUFFICIENT_EVIDENCE: 0.0,
}


class PortfolioOpinionAggregationService:
    def __init__(self, config: PortfolioManagerConfig | None = None) -> None:
        self.config = config or PortfolioManagerConfig()

    def contributions(self, opinions: list[AnalystOpinion]) -> list[AnalystContribution]:
        result: list[AnalystContribution] = []
        for opinion in sorted(opinions, key=lambda item: (item.ticker, item.analyst_id, item.opinion_id)):
            orientation = ORIENTATION[opinion.direction]
            freshness = self.config.stale_opinion_factor if opinion.data_freshness.is_stale else 1.0
            quality = fmean(item.confidence for item in opinion.evidence) if opinion.evidence else 0.0
            confidence = opinion.confidence.value
            result.append(
                AnalystContribution(
                    opinion_id=opinion.opinion_id,
                    analyst_id=opinion.analyst_id,
                    analyst_role=opinion.analyst_role,
                    symbol=opinion.ticker.upper(),
                    orientation=orientation,
                    confidence=confidence,
                    freshness_factor=freshness,
                    data_quality_factor=quality,
                    signed_contribution=orientation * confidence * freshness * quality,
                )
            )
        return result

    @staticmethod
    def agreement(contributions: list[AnalystContribution]) -> dict[str, tuple[float, float, float]]:
        grouped: dict[str, list[AnalystContribution]] = defaultdict(list)
        for contribution in contributions:
            grouped[contribution.symbol].append(contribution)
        result: dict[str, tuple[float, float, float]] = {}
        for symbol, items in grouped.items():
            signs = [item.orientation for item in items if item.orientation != 0]
            agreement = abs(sum(signs)) / len(signs) if signs else 0.0
            confidences = [item.confidence for item in items]
            result[symbol] = (agreement, 1.0 - agreement, pstdev(confidences) if len(confidences) > 1 else 0.0)
        return result
