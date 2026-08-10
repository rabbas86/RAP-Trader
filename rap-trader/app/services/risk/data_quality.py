"""Completeness, freshness, and classification quality checks."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.domain.models.market_data import HistoricalBarsResult
from app.domain.models.portfolio import PortfolioProposal


class RiskDataQualityService:
    def calculate(
        self,
        proposal: PortfolioProposal,
        history: list[HistoricalBarsResult],
        liquidity: dict[str, dict[str, float]],
        minimum: int,
        stale_tolerance: timedelta,
    ) -> dict[str, Any]:
        symbols = {item.symbol for item in proposal.positions}
        by_symbol = {str(item.symbol): item for item in history}
        issues: list[str] = []
        for symbol in sorted(symbols):
            item = by_symbol.get(symbol)
            if item is None:
                issues.append(f"{symbol}: missing market history")
            elif len(item.bars) - 1 < minimum:
                issues.append(f"{symbol}: insufficient history")
            elif proposal.as_of - item.actual_end > stale_tolerance:
                issues.append(f"{symbol}: stale market history")
            if symbol not in liquidity:
                issues.append(f"{symbol}: missing liquidity inputs")
        unknown = sum(1 for item in proposal.positions if item.sector is None or item.industry is None)
        if unknown:
            issues.append(f"{unknown} positions have unknown classifications")
        checks = max(1, len(symbols) * 3)
        return {
            "score": max(0.0, 1.0 - len(issues) / checks),
            "issues": tuple(issues),
            "sufficient": not any(
                "missing market" in issue or "insufficient history" in issue or "stale market" in issue for issue in issues
            ),
        }
