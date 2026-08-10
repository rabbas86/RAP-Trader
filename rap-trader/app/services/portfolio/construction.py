"""Conviction-weighted deterministic portfolio construction."""

from app.domain.models.portfolio import AssetConviction, PortfolioConstraintSet, PortfolioPosition, ResearchPortfolio
from app.services.portfolio.config import PortfolioManagerConfig
from app.services.portfolio.constraints import ConstraintEngine
from app.services.portfolio.turnover import compute_turnover, scale_to_turnover


class PortfolioConstructionService:
    def __init__(self, config: PortfolioManagerConfig | None = None) -> None:
        self.config = config or PortfolioManagerConfig()
        self.engine = ConstraintEngine()

    def construct(
        self,
        portfolio: ResearchPortfolio,
        convictions: list[AssetConviction],
        constraints: PortfolioConstraintSet,
        universe: tuple[str, ...],
    ) -> tuple[dict[str, float], list[str], float]:
        current = {position.symbol: position.weight for position in portfolio.positions}
        metadata = {position.symbol: position for position in portfolio.positions}
        scores = {
            item.symbol: item.conviction
            for item in convictions
            if item.symbol in universe and abs(item.conviction) >= self.config.weak_conviction_threshold
        }
        if len(scores) < constraints.min_positions:
            fallback = sorted(convictions, key=lambda item: (-abs(item.conviction), item.symbol))
            for item in fallback:
                if item.symbol in universe and item.conviction != 0:
                    scores.setdefault(item.symbol, item.conviction)
                if len(scores) >= constraints.min_positions:
                    break
        denominator = sum(abs(score) for score in scores.values())
        budget = min(constraints.max_gross_exposure, 1.0 - constraints.min_cash_weight)
        proposed = {symbol: (budget * score / denominator if denominator else 0.0) for symbol, score in scores.items()}
        for symbol in universe:
            proposed.setdefault(symbol, 0.0)
            metadata.setdefault(symbol, PortfolioPosition(symbol=symbol, weight=0.0))
        bounded, adjustments = self.engine.apply(proposed, metadata, constraints)
        scaled, turnover, was_scaled = scale_to_turnover(current, bounded, constraints.max_turnover)
        if was_scaled:
            adjustments.append("portfolio:turnover_cap")
            scaled, more = self.engine.apply(scaled, metadata, constraints)
            adjustments.extend(more)
            turnover = compute_turnover(current, scaled)
        active_count = sum(weight != 0 for weight in scaled.values())
        if active_count < constraints.min_positions:
            adjustments.append("portfolio:min_positions_unmet")
        return scaled, adjustments, turnover
