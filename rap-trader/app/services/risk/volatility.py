"""Deterministic historical volatility calculations."""

from __future__ import annotations

import math
from itertools import pairwise
from statistics import stdev
from typing import Any

from app.domain.models.market_data import HistoricalBarsResult
from app.domain.models.portfolio import PortfolioProposal


def returns(item: HistoricalBarsResult) -> list[float]:
    return [current.close / previous.close - 1.0 for previous, current in pairwise(item.bars)]


class VolatilityRiskService:
    def calculate(self, proposal: PortfolioProposal, history: list[HistoricalBarsResult], minimum: int) -> dict[str, Any]:
        by_symbol = {str(item.symbol): returns(item) for item in history}
        valid = {symbol: values for symbol, values in by_symbol.items() if len(values) >= minimum}
        per_asset = {symbol: stdev(values) * math.sqrt(252) for symbol, values in sorted(valid.items())}
        aligned = min((len(values) for values in valid.values()), default=0)
        portfolio_returns = (
            [
                sum(
                    position.proposed_weight * valid.get(position.symbol, [0.0] * aligned)[-aligned + index]
                    for position in proposal.positions
                )
                for index in range(aligned)
            ]
            if aligned
            else []
        )
        portfolio = stdev(portfolio_returns) * math.sqrt(252) if len(portfolio_returns) >= 2 else None
        return {
            "per_asset": per_asset,
            "portfolio_volatility": portfolio,
            "sample_size": aligned,
            "missing": sorted({p.symbol for p in proposal.positions} - valid.keys()),
        }
