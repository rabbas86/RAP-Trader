"""Long, short, cash, and leverage exposure review."""

from __future__ import annotations

from app.domain.models.portfolio import PortfolioProposal


class ExposureRiskService:
    def calculate(self, proposal: PortfolioProposal) -> dict[str, float]:
        values = [item.proposed_weight for item in proposal.positions]
        gross = sum(abs(value) for value in values)
        return {
            "gross_exposure": gross,
            "net_exposure": sum(values),
            "long_exposure": sum(value for value in values if value > 0),
            "short_exposure": sum(abs(value) for value in values if value < 0),
            "cash_weight": proposal.cash_weight,
            "implied_leverage": max(1.0, gross) if not any(value < 0 for value in values) else gross,
        }
