"""Transparent bounded asset conviction calculation."""

from collections import defaultdict
from statistics import fmean, pstdev

from app.domain.models.portfolio import AnalystContribution, AssetConviction
from app.services.portfolio.config import PortfolioManagerConfig


class AssetConvictionService:
    def __init__(self, config: PortfolioManagerConfig | None = None) -> None:
        self.config = config or PortfolioManagerConfig()

    def calculate(self, contributions: list[AnalystContribution]) -> list[AssetConviction]:
        grouped: dict[str, list[AnalystContribution]] = defaultdict(list)
        for contribution in contributions:
            grouped[contribution.symbol].append(contribution)
        result: list[AssetConviction] = []
        for symbol in sorted(grouped):
            items = grouped[symbol]
            signs = [item.orientation for item in items if item.orientation != 0]
            agreement = abs(sum(signs)) / len(signs) if signs else 0.0
            confidence = fmean(item.confidence for item in items)
            dispersion = pstdev(item.confidence for item in items) if len(items) > 1 else 0.0
            raw = fmean(item.signed_contribution for item in items)
            sufficient = len(items) >= self.config.minimum_analyst_coverage
            conviction = max(-1.0, min(1.0, raw * (1.0 - dispersion))) if sufficient else 0.0
            result.append(
                AssetConviction(
                    symbol=symbol,
                    conviction=conviction,
                    agreement=agreement,
                    disagreement=1.0 - agreement,
                    confidence_mean=confidence,
                    confidence_dispersion=dispersion,
                    coverage=len(items),
                    contributions=tuple(items),
                    sufficient_coverage=sufficient,
                )
            )
        return result
