"""Offline deterministic research portfolio manager."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict

from app.domain.models.analyst import AnalystOpinion
from app.domain.models.portfolio import (
    PortfolioConstraintSet,
    PortfolioProposal,
    PortfolioProposalPosition,
    ResearchPortfolio,
)
from app.services.portfolio.config import PortfolioManagerConfig
from app.services.portfolio.construction import PortfolioConstructionService
from app.services.portfolio.conviction import AssetConvictionService
from app.services.portfolio.opinions import PortfolioOpinionAggregationService
from app.services.portfolio.provenance import PortfolioProvenanceService
from app.services.portfolio.trace import build_portfolio_trace
from app.services.portfolio.universe import PortfolioUniverse
from app.services.portfolio.validation import PortfolioInputValidationService


class PortfolioProposalRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    portfolio: ResearchPortfolio
    opinions: list[AnalystOpinion]
    constraints: PortfolioConstraintSet = PortfolioConstraintSet()
    as_of: datetime | None = None
    symbols: list[str] | None = None


class PortfolioManagerService:
    def __init__(self, config: PortfolioManagerConfig | None = None) -> None:
        self.config = config or PortfolioManagerConfig()
        self.validation = PortfolioInputValidationService()
        self.aggregation = PortfolioOpinionAggregationService(self.config)
        self.conviction = AssetConvictionService(self.config)
        self.construction = PortfolioConstructionService(self.config)
        self.provenance = PortfolioProvenanceService()

    def health(self) -> dict[str, object]:
        return {"status": "ok", "offline": True, "research_only": True, "checked_at": datetime.now(UTC).isoformat()}

    def metadata(self) -> dict[str, object]:
        return {
            "component": "portfolio-manager",
            "algorithm_version": self.config.algorithm_version,
            "deterministic": True,
            "offline": True,
            "research_only": True,
            "suitable_for_live_trading": False,
            "decision_ready": False,
            "output": "PortfolioProposal",
        }

    def validate(self, request: PortfolioProposalRequest) -> dict[str, object]:
        as_of = self._as_of(request)
        accepted = self.validation.validate_opinions(request.opinions, as_of)
        return {"valid": True, "accepted_opinions": len(accepted), "ignored_opinions": len(request.opinions) - len(accepted)}

    def propose(self, request: PortfolioProposalRequest) -> PortfolioProposal:
        as_of = self._as_of(request)
        opinions = self.validation.validate_opinions(request.opinions, as_of)
        universe = PortfolioUniverse.build(request.portfolio, opinions, request.constraints, request.symbols)
        contributions = self.aggregation.contributions(opinions)
        convictions = self.conviction.calculate(contributions)
        weights, adjustments, turnover = self.construction.construct(request.portfolio, convictions, request.constraints, universe.symbols)
        input_fingerprint = self.provenance.fingerprint(
            {
                "portfolio": request.portfolio.model_dump(mode="json"),
                "opinions": [item.model_dump(mode="json") for item in opinions],
                "as_of": as_of,
            }
        )
        config_fingerprint = self.provenance.fingerprint(self.config)
        constraint_fingerprint = self.provenance.fingerprint(request.constraints)
        proposal_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{request.portfolio.portfolio_id}|{input_fingerprint}|{config_fingerprint}|{constraint_fingerprint}",
            )
        )
        trace = build_portfolio_trace(proposal_id, convictions, adjustments, as_of)
        current = {position.symbol: position for position in request.portfolio.positions}
        conviction_by_symbol = {item.symbol: item.conviction for item in convictions}
        positions = tuple(
            PortfolioProposalPosition(
                symbol=symbol,
                current_weight=current[symbol].weight if symbol in current else 0.0,
                proposed_weight=weight,
                conviction=conviction_by_symbol.get(symbol, 0.0),
                sector=current[symbol].sector if symbol in current else None,
                industry=current[symbol].industry if symbol in current else None,
                asset_class=current[symbol].asset_class if symbol in current else "equity",
                adjustments=tuple(item for item in adjustments if item.startswith(f"{symbol}:")),
            )
            for symbol, weight in sorted(weights.items())
            if weight != 0
        )
        net = sum(position.proposed_weight for position in positions)
        gross = sum(abs(position.proposed_weight) for position in positions)
        return PortfolioProposal(
            proposal_id=proposal_id,
            portfolio_id=request.portfolio.portfolio_id,
            as_of=as_of,
            positions=positions,
            cash_weight=1.0 - net,
            gross_exposure=gross,
            net_exposure=net,
            turnover=turnover,
            adjustments=tuple(adjustments),
            opinion_ids=tuple(sorted(opinion.opinion_id for opinion in opinions)),
            input_fingerprint=input_fingerprint,
            config_fingerprint=config_fingerprint,
            constraint_fingerprint=constraint_fingerprint,
            algorithm_version=self.config.algorithm_version,
            git_commit=self.provenance.git_commit(),
            trace=trace,
        )

    @staticmethod
    def _as_of(request: PortfolioProposalRequest) -> datetime:
        as_of = request.as_of or request.portfolio.as_of
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must include timezone information")
        as_of = as_of.astimezone(UTC)
        if as_of > datetime.now(UTC):
            raise ValueError("future as_of timestamps are forbidden")
        if request.portfolio.as_of > as_of:
            raise ValueError("portfolio snapshot is from the future relative to as_of")
        return as_of


PortfolioService = PortfolioManagerService
