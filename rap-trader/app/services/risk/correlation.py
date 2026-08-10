"""Pairwise correlation and cluster risk."""

from __future__ import annotations

from typing import Any

from app.domain.models.market_data import HistoricalBarsResult
from app.domain.models.portfolio import PortfolioProposal
from app.services.portfolio.correlation import PortfolioCorrelationService


class CorrelationRiskService:
    def calculate(
        self, proposal: PortfolioProposal, history: list[HistoricalBarsResult], minimum: int, cluster_threshold: float = 0.7
    ) -> dict[str, Any]:
        pairs = PortfolioCorrelationService(minimum).correlations(history, proposal.as_of)
        weights = {item.symbol: abs(item.proposed_weight) for item in proposal.positions}
        usable = [(pair, value) for pair, value in pairs.items() if pair[0] != pair[1] and value is not None]
        denominator = sum(weights.get(left, 0) * weights.get(right, 0) for (left, right), _ in usable)
        average = (
            sum(weights.get(left, 0) * weights.get(right, 0) * value for (left, right), value in usable) / denominator
            if denominator
            else None
        )
        maximum = max((value for _, value in usable), default=None)
        edges = sorted(pair for pair, value in usable if value >= cluster_threshold)
        clusters = self._clusters(edges)
        return {
            "weighted_average_correlation": average,
            "max_pairwise_correlation": maximum,
            "high_correlation_clusters": clusters,
            "pair_count": len(usable),
            "diversification_penalty": max(0.0, average or 0.0),
        }

    @staticmethod
    def _clusters(edges: list[tuple[str, str]]) -> tuple[tuple[str, ...], ...]:
        groups: list[set[str]] = []
        for left, right in edges:
            matched = [group for group in groups if left in group or right in group]
            if not matched:
                groups.append({left, right})
            else:
                merged = {left, right}.union(*matched)
                groups = [group for group in groups if group not in matched] + [merged]
        return tuple(sorted(tuple(sorted(group)) for group in groups))
