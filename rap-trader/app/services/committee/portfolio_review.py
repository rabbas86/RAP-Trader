"""Portfolio-proposal review; never reconstructs or optimizes a proposal."""

from pydantic import BaseModel, ConfigDict

from app.domain.models.portfolio import PortfolioProposal
from app.services.committee.config import CommitteeConfig
from app.services.committee.research_case import ResearchCase


class PortfolioReview(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)
    acceptable: bool
    findings: tuple[str, ...]
    required_modifications: tuple[str, ...]


class CommitteePortfolioReviewService:
    def review(self, proposal: PortfolioProposal, case: ResearchCase, config: CommitteeConfig) -> PortfolioReview:
        findings: list[str] = []
        modifications: list[str] = []
        for position in proposal.positions:
            if position.proposed_weight > config.maximum_position_weight:
                findings.append(f"Concentration exceeds committee policy for {position.symbol}")
                modifications.append(f"Reduce {position.symbol} weight to at most {config.maximum_position_weight:.2f}")
            if position.proposed_weight > 0.10 and abs(position.conviction) < 0.25:
                findings.append(f"Low-confidence conviction is overweight for {position.symbol}")
                modifications.append(f"Reduce low-conviction allocation for {position.symbol}")
        if case.views and sum(view.confidence for view in case.views) / len(case.views) < 0.5 and proposal.cash_weight < 0.15:
            findings.append("Cash is low relative to research uncertainty")
            modifications.append("Increase cash while research uncertainty remains high")
        return PortfolioReview(acceptable=not modifications, findings=tuple(findings), required_modifications=tuple(modifications))
