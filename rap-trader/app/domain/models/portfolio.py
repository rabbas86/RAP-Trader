"""Strict research-only portfolio construction contracts."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.models.analyst import AnalysisTrace, AnalystRole
from app.domain.models.market_data import UtcDatetime, _require_aware_utc

WEIGHT_TOLERANCE = 1e-8


def _portfolio_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value):
        raise ValueError("invalid portfolio identifier")
    return value


PortfolioId = Annotated[str, Field(min_length=1, max_length=128), AfterValidator(_portfolio_id)]


class _PortfolioModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)


class _ResearchOnly(_PortfolioModel):
    research_only: Literal[True] = True
    suitable_for_live_trading: Literal[False] = False
    decision_ready: Literal[False] = False


class PortfolioContext(BaseModel):
    """Legacy risk context retained for backward compatibility."""

    equity: float = Field(gt=0, allow_inf_nan=False)
    current_drawdown_percent: float = Field(ge=0, allow_inf_nan=False)
    daily_loss_percent: float = Field(ge=0, allow_inf_nan=False)


class PortfolioPosition(_PortfolioModel):
    symbol: str = Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9]+(?:[.-][A-Z0-9]+)*$")
    weight: float = Field(ge=-1, le=1, allow_inf_nan=False)
    sector: str | None = None
    industry: str | None = None
    asset_class: str = Field(default="equity", min_length=1)

    @field_validator("symbol", mode="before")
    @classmethod
    def normalize_symbol(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class ResearchPortfolio(_ResearchOnly):
    portfolio_id: PortfolioId
    as_of: UtcDatetime
    positions: tuple[PortfolioPosition, ...] = ()
    cash_weight: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)

    @field_validator("as_of")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        result = _require_aware_utc(value)
        if result > datetime.now(UTC):
            raise ValueError("future portfolio timestamps are forbidden")
        return result

    @model_validator(mode="after")
    def invariants(self) -> ResearchPortfolio:
        symbols = [position.symbol for position in self.positions]
        if len(symbols) != len(set(symbols)):
            raise ValueError("portfolio symbols must be unique")
        if abs(sum(position.weight for position in self.positions) + self.cash_weight - 1.0) > WEIGHT_TOLERANCE:
            raise ValueError("portfolio weights and cash must sum to one")
        return self


class AnalystContribution(_PortfolioModel):
    opinion_id: str = Field(min_length=1)
    analyst_id: str = Field(min_length=1)
    analyst_role: AnalystRole
    symbol: str = Field(min_length=1)
    orientation: float = Field(ge=-1, le=1, allow_inf_nan=False)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    freshness_factor: float = Field(ge=0, le=1, allow_inf_nan=False)
    data_quality_factor: float = Field(ge=0, le=1, allow_inf_nan=False)
    signed_contribution: float = Field(ge=-1, le=1, allow_inf_nan=False)


class AssetConviction(_PortfolioModel):
    symbol: str = Field(min_length=1)
    conviction: float = Field(ge=-1, le=1, allow_inf_nan=False)
    agreement: float = Field(ge=0, le=1, allow_inf_nan=False)
    disagreement: float = Field(ge=0, le=1, allow_inf_nan=False)
    confidence_mean: float = Field(ge=0, le=1, allow_inf_nan=False)
    confidence_dispersion: float = Field(ge=0, le=1, allow_inf_nan=False)
    coverage: int = Field(ge=0)
    contributions: tuple[AnalystContribution, ...] = ()
    sufficient_coverage: bool


class PortfolioConstraintSet(_ResearchOnly):
    max_position_weight: float = Field(default=0.20, gt=0, le=1, allow_inf_nan=False)
    min_position_weight: float = Field(default=0.0, ge=0, le=1, allow_inf_nan=False)
    max_sector_weight: float = Field(default=0.40, gt=0, le=1, allow_inf_nan=False)
    max_industry_weight: float = Field(default=0.30, gt=0, le=1, allow_inf_nan=False)
    max_asset_class_weight: float = Field(default=1.0, gt=0, le=1, allow_inf_nan=False)
    min_cash_weight: float = Field(default=0.05, ge=0, le=1, allow_inf_nan=False)
    max_gross_exposure: float = Field(default=0.95, gt=0, le=2, allow_inf_nan=False)
    min_net_exposure: float = Field(default=0.0, ge=-1, le=1, allow_inf_nan=False)
    max_net_exposure: float = Field(default=0.95, ge=-1, le=1, allow_inf_nan=False)
    min_positions: int = Field(default=0, ge=0)
    max_positions: int = Field(default=20, gt=0)
    allow_shorts: bool = False
    max_short_position_weight: float = Field(default=0.10, ge=0, le=1, allow_inf_nan=False)
    max_turnover: float = Field(default=1.0, ge=0, le=2, allow_inf_nan=False)
    sector_caps: dict[str, float] = Field(default_factory=dict)
    industry_caps: dict[str, float] = Field(default_factory=dict)
    asset_class_caps: dict[str, float] = Field(default_factory=dict)
    excluded_symbols: frozenset[str] = frozenset()

    @field_validator("sector_caps", "industry_caps", "asset_class_caps")
    @classmethod
    def finite_caps(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not math.isfinite(cap) or cap < 0 or cap > 1 for cap in value.values()):
            raise ValueError("constraint caps must be finite values in [0, 1]")
        return value

    @model_validator(mode="after")
    def ranges(self) -> PortfolioConstraintSet:
        if self.min_positions > self.max_positions:
            raise ValueError("min_positions cannot exceed max_positions")
        if self.min_net_exposure > self.max_net_exposure:
            raise ValueError("min_net_exposure cannot exceed max_net_exposure")
        return self


class PortfolioProposalPosition(_PortfolioModel):
    symbol: str = Field(min_length=1)
    current_weight: float = Field(ge=-1, le=1, allow_inf_nan=False)
    proposed_weight: float = Field(ge=-1, le=1, allow_inf_nan=False)
    conviction: float = Field(ge=-1, le=1, allow_inf_nan=False)
    sector: str | None = None
    industry: str | None = None
    asset_class: str = Field(default="equity", min_length=1)
    adjustments: tuple[str, ...] = ()

    @property
    def target_weight(self) -> float:
        return self.proposed_weight


class PortfolioProposal(_ResearchOnly):
    proposal_id: PortfolioId
    portfolio_id: PortfolioId
    as_of: UtcDatetime
    positions: tuple[PortfolioProposalPosition, ...]
    cash_weight: float = Field(ge=0, le=1, allow_inf_nan=False)
    gross_exposure: float = Field(ge=0, le=2, allow_inf_nan=False)
    net_exposure: float = Field(ge=-1, le=1, allow_inf_nan=False)
    turnover: float = Field(ge=0, le=2, allow_inf_nan=False)
    adjustments: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    opinion_ids: tuple[str, ...] = ()
    input_fingerprint: str = Field(min_length=1)
    config_fingerprint: str = Field(min_length=1)
    constraint_fingerprint: str = Field(min_length=1)
    algorithm_version: str = Field(min_length=1)
    git_commit: str | None = None
    trace: AnalysisTrace

    @field_validator("as_of")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        result = _require_aware_utc(value)
        if result > datetime.now(UTC):
            raise ValueError("future proposal timestamps are forbidden")
        return result

    @model_validator(mode="after")
    def weights(self) -> PortfolioProposal:
        if abs(sum(position.proposed_weight for position in self.positions) + self.cash_weight - 1.0) > WEIGHT_TOLERANCE:
            raise ValueError("proposal weights and cash must sum to one")
        if abs(sum(abs(position.proposed_weight) for position in self.positions) - self.gross_exposure) > WEIGHT_TOLERANCE:
            raise ValueError("gross exposure does not match positions")
        if abs(sum(position.proposed_weight for position in self.positions) - self.net_exposure) > WEIGHT_TOLERANCE:
            raise ValueError("net exposure does not match positions")
        return self
