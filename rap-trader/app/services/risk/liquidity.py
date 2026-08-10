"""Liquidity review based exclusively on supplied observations."""

from __future__ import annotations

from typing import Any

from app.domain.models.portfolio import PortfolioProposal


class LiquidityRiskService:
    def calculate(
        self, proposal: PortfolioProposal, observations: dict[str, dict[str, float]], illiquid_threshold: float
    ) -> dict[str, Any]:
        scores: dict[str, float] = {}
        warnings: list[str] = []
        illiquid_weight = 0.0
        for position in proposal.positions:
            item = observations.get(position.symbol)
            if item is None:
                warnings.append(f"{position.symbol}: liquidity inputs unavailable")
                continue
            dollar_volume = item.get("average_dollar_volume")
            if dollar_volume is None:
                average_volume, price = item.get("average_volume"), item.get("price")
                dollar_volume = average_volume * price if average_volume is not None and price is not None else None
            if dollar_volume is None or dollar_volume < 0:
                warnings.append(f"{position.symbol}: dollar volume unavailable")
                continue
            score = min(1.0, dollar_volume / illiquid_threshold)
            spread = item.get("bid_ask_spread")
            if spread is not None:
                score *= max(0.0, 1.0 - min(1.0, spread * 20.0))
            scores[position.symbol] = score
            if score < 0.5:
                illiquid_weight += abs(position.proposed_weight)
        return {
            "scores": scores,
            "liquidity_score": sum(scores.values()) / len(scores) if scores else 0.0,
            "illiquid_weight": illiquid_weight,
            "warnings": tuple(warnings),
            "portfolio_value_known": False,
        }
