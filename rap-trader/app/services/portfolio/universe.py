"""Explicit deterministic portfolio universe selection."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.models.analyst import AnalystOpinion
from app.domain.models.portfolio import PortfolioConstraintSet, ResearchPortfolio


@dataclass(frozen=True)
class PortfolioUniverse:
    symbols: tuple[str, ...]

    @classmethod
    def build(
        cls,
        portfolio: ResearchPortfolio,
        opinions: list[AnalystOpinion],
        constraints: PortfolioConstraintSet,
        explicit_symbols: list[str] | None = None,
    ) -> PortfolioUniverse:
        symbols = (
            set(explicit_symbols or ())
            | {position.symbol for position in portfolio.positions}
            | {opinion.ticker.upper() for opinion in opinions}
        )
        symbols -= {symbol.upper() for symbol in constraints.excluded_symbols}
        return cls(tuple(sorted(symbols)))
