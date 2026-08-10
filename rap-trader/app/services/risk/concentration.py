"""Portfolio concentration metrics."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.domain.models.portfolio import PortfolioProposal


class ConcentrationRiskService:
    def calculate(self, proposal: PortfolioProposal) -> dict[str, Any]:
        weights = sorted((abs(item.proposed_weight) for item in proposal.positions), reverse=True)
        hhi = sum(value * value for value in weights)
        result: dict[str, Any] = {
            "max_single_position_weight": max(weights, default=0.0),
            "hhi": hhi,
            "effective_positions": 1.0 / hhi if hhi else 0.0,
            "top_3_weight": sum(weights[:3]),
            "top_5_weight": sum(weights[:5]),
        }
        for field_name in ("sector", "industry", "asset_class"):
            groups: defaultdict[str, float] = defaultdict(float)
            for position in proposal.positions:
                key = getattr(position, field_name) or "unknown"
                groups[key] += abs(position.proposed_weight)
            result[f"{field_name}_weights"] = dict(sorted(groups.items()))
            result[f"max_{field_name}_weight"] = max(groups.values(), default=0.0)
        result["unknown_classification_weight"] = sum(
            abs(item.proposed_weight) for item in proposal.positions if item.sector is None or item.industry is None
        )
        return result
