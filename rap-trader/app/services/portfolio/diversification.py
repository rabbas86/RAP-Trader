"""Pure portfolio diversification metrics."""

from collections import defaultdict

from app.domain.models.portfolio import PortfolioProposalPosition


class DiversificationService:
    @staticmethod
    def hhi(weights: list[float]) -> float:
        return sum(abs(weight) ** 2 for weight in weights)

    def effective_positions(self, weights: list[float]) -> float:
        hhi = self.hhi(weights)
        return 0.0 if hhi == 0 else 1.0 / hhi

    @staticmethod
    def group_concentration(positions: list[PortfolioProposalPosition], field: str = "sector") -> dict[str, float]:
        result: dict[str, float] = defaultdict(float)
        for position in positions:
            group = getattr(position, field) or "UNKNOWN"
            result[group] += abs(position.proposed_weight)
        return dict(sorted(result.items()))

    @staticmethod
    def correlation_concentration(
        weights: dict[str, float], correlations: dict[tuple[str, str], float | None], threshold: float = 0.7
    ) -> float:
        total = 0.0
        for (left, right), correlation in correlations.items():
            if left != right and correlation is not None and correlation >= threshold:
                total += abs(weights.get(left, 0.0) * weights.get(right, 0.0))
        return total
