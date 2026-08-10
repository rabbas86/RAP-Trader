"""Proposal turnover review without transaction-cost estimates."""

from __future__ import annotations

from app.domain.models.portfolio import PortfolioProposal


class TurnoverReviewService:
    @staticmethod
    def calculate(proposal: PortfolioProposal, maximum: float) -> tuple[float, tuple[str, ...]]:
        warnings = ("Proposal turnover exceeds the configured research limit",) if proposal.turnover > maximum else ()
        return proposal.turnover, warnings
