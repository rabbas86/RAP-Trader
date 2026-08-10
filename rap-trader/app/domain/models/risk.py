"""Strict risk contracts for legacy and portfolio-level research risk."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.models.analyst import AnalysisTrace
from app.domain.models.market_data import UtcDatetime, _require_aware_utc


class LegacyRiskAssessment(BaseModel):
    """Phase 1 pre-execution assessment retained without schema changes."""

    approved: bool
    rejection_reasons: list[str]
    maximum_allowed_quantity: int = Field(ge=0)
    estimated_position_percent: float = Field(ge=0)
    estimated_daily_loss_percent: float = Field(ge=0)


class RiskSeverity(StrEnum):
    INFO = "info"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class RiskDecisionType(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUIRE_MODIFICATION = "require_modification"
    INSUFFICIENT_DATA = "insufficient_data"


class RiskModificationType(StrEnum):
    REDUCE_SYMBOL_WEIGHT = "reduce_symbol_weight"
    REDUCE_SECTOR_EXPOSURE = "reduce_sector_exposure"
    REDUCE_INDUSTRY_EXPOSURE = "reduce_industry_exposure"
    REDUCE_ASSET_CLASS_EXPOSURE = "reduce_asset_class_exposure"
    INCREASE_CASH = "increase_cash"
    REDUCE_GROSS_EXPOSURE = "reduce_gross_exposure"
    REDUCE_NET_EXPOSURE = "reduce_net_exposure"
    REDUCE_SHORT_EXPOSURE = "reduce_short_exposure"
    REDUCE_TURNOVER = "reduce_turnover"
    REDUCE_CORRELATED_CLUSTER = "reduce_correlated_cluster"
    REMOVE_OR_REDUCE_ILLIQUID_ASSET = "remove_or_reduce_illiquid_asset"
    IMPROVE_DATA_QUALITY = "improve_data_quality"
    REFRESH_STALE_DATA = "refresh_stale_data"
    REDUCE_VAR = "reduce_var"
    REDUCE_CVAR = "reduce_cvar"
    REDUCE_VOLATILITY = "reduce_volatility"
    REDUCE_DRAWDOWN_EXPOSURE = "reduce_drawdown_exposure"
    OTHER = "other"


class RiskCategory(StrEnum):
    CONCENTRATION = "concentration"
    DIVERSIFICATION = "diversification"
    VOLATILITY = "volatility"
    DRAWDOWN = "drawdown"
    CORRELATION = "correlation"
    LIQUIDITY = "liquidity"
    LEVERAGE = "leverage"
    GROSS_EXPOSURE = "gross_exposure"
    NET_EXPOSURE = "net_exposure"
    TURNOVER = "turnover"
    SECTOR = "sector"
    INDUSTRY = "industry"
    ASSET_CLASS = "asset_class"
    CASH = "cash"
    SHORT_EXPOSURE = "short_exposure"
    VAR = "var"
    CVAR = "cvar"
    SCENARIO = "scenario"
    STRESS = "stress"
    DATA_QUALITY = "data_quality"
    STALE_DATA = "stale_data"
    UNKNOWN = "unknown"


class _RiskModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)


class _ResearchOnly(_RiskModel):
    research_only: Literal[True] = True
    suitable_for_live_trading: Literal[False] = False
    decision_ready: Literal[False] = False


class RiskLimit(_RiskModel):
    limit_id: str = Field(min_length=1)
    category: RiskCategory
    metric: str = Field(min_length=1)
    threshold: float = Field(allow_inf_nan=False)
    comparator: Literal["gt", "gte", "lt", "lte", "eq", "ne"]
    severity: RiskSeverity
    hard_limit: bool
    description: str = Field(min_length=1)


class RiskMetric(_RiskModel):
    metric_id: str = Field(min_length=1)
    category: RiskCategory
    name: str = Field(min_length=1)
    value: float = Field(allow_inf_nan=False)
    units: str = Field(min_length=1)
    as_of: UtcDatetime
    source_fingerprint: str = Field(min_length=1)
    valid: bool = True
    warnings: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()

    @field_validator("as_of")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        result = _require_aware_utc(value)
        if result > datetime.now(UTC):
            raise ValueError("future risk metric timestamps are forbidden")
        return result


class RiskBreach(_RiskModel):
    breach_id: str = Field(min_length=1)
    limit_id: str = Field(min_length=1)
    category: RiskCategory
    metric_name: str = Field(min_length=1)
    observed_value: float = Field(allow_inf_nan=False)
    threshold: float = Field(allow_inf_nan=False)
    severity: RiskSeverity
    hard_limit: bool
    description: str = Field(min_length=1)
    recommended_action: str = Field(min_length=1)
    provenance: str = Field(min_length=1)


class StressScenario(_RiskModel):
    scenario_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    shocks: dict[str, float]
    source: str = Field(min_length=1)
    version: str = Field(default="1", min_length=1)
    deterministic: bool = True
    assumptions: tuple[str, ...] = ()

    @field_validator("shocks")
    @classmethod
    def finite_shocks(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not math.isfinite(item) for item in value.values()):
            raise ValueError("stress shocks must be finite")
        return value


class StressResult(_RiskModel):
    scenario_id: str = Field(min_length=1)
    estimated_portfolio_impact: float = Field(allow_inf_nan=False)
    affected_positions: dict[str, float]
    concentration_effect: float = Field(allow_inf_nan=False)
    liquidity_effect: float = Field(allow_inf_nan=False)
    warnings: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()

    @field_validator("affected_positions")
    @classmethod
    def finite_positions(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not math.isfinite(item) for item in value.values()):
            raise ValueError("stress position impacts must be finite")
        return value


class RiskModification(_RiskModel):
    modification_type: RiskModificationType
    symbol: str | None = None
    category: RiskCategory | None = None
    current_value: float = Field(allow_inf_nan=False)
    recommended_maximum: float | None = Field(default=None, allow_inf_nan=False)
    recommended_minimum: float | None = Field(default=None, allow_inf_nan=False)
    reason: str = Field(min_length=1)
    associated_breach_ids: tuple[str, ...] = ()


class RiskConstraintSet(_ResearchOnly):
    max_single_position_weight: float = Field(default=0.20, gt=0, le=1, allow_inf_nan=False)
    max_sector_weight: float = Field(default=0.35, gt=0, le=1, allow_inf_nan=False)
    max_industry_weight: float = Field(default=0.25, gt=0, le=1, allow_inf_nan=False)
    max_asset_class_weight: float = Field(default=0.90, gt=0, le=1, allow_inf_nan=False)
    max_hhi: float = Field(default=0.20, gt=0, le=1, allow_inf_nan=False)
    min_effective_positions: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    max_portfolio_volatility: float = Field(default=0.30, gt=0, allow_inf_nan=False)
    maximum_average_correlation: float = Field(default=0.75, ge=-1, le=1, allow_inf_nan=False)
    max_pairwise_correlation: float = Field(
        default=0.90,
        ge=-1,
        le=1,
        allow_inf_nan=False,
        validation_alias=AliasChoices("max_pairwise_correlation", "maximum_pair_correlation"),
    )
    max_drawdown: float = Field(default=0.25, gt=0, le=1, allow_inf_nan=False)
    max_var_95: float = Field(default=0.05, gt=0, le=1, allow_inf_nan=False, validation_alias=AliasChoices("max_var_95", "maximum_var_95"))
    max_cvar_95: float = Field(
        default=0.08, gt=0, le=1, allow_inf_nan=False, validation_alias=AliasChoices("max_cvar_95", "maximum_cvar_95")
    )
    max_var_99: float = Field(default=0.10, gt=0, le=1, allow_inf_nan=False, validation_alias=AliasChoices("max_var_99", "maximum_var_99"))
    max_cvar_99: float = Field(
        default=0.15, gt=0, le=1, allow_inf_nan=False, validation_alias=AliasChoices("max_cvar_99", "maximum_cvar_99")
    )
    minimum_liquidity_score: float = Field(default=0.50, ge=0, le=1, allow_inf_nan=False)
    max_illiquid_weight: float = Field(
        default=0.20, ge=0, le=1, allow_inf_nan=False, validation_alias=AliasChoices("max_illiquid_weight", "maximum_illiquid_weight")
    )
    maximum_unknown_metadata_weight: float = Field(default=0.10, ge=0, le=1, allow_inf_nan=False)
    max_gross_exposure: float = Field(default=1.25, gt=0, le=5, allow_inf_nan=False)
    max_net_exposure: float = Field(default=1.0, ge=0, le=5, allow_inf_nan=False)
    max_short_exposure: float = Field(default=0.20, ge=0, le=2, allow_inf_nan=False)
    min_cash_weight: float = Field(default=0.02, ge=0, le=1, allow_inf_nan=False)
    max_turnover: float = Field(default=0.50, ge=0, le=2, allow_inf_nan=False)
    min_sample_size: int = Field(default=20, ge=2)
    stale_data_tolerance: timedelta = timedelta(days=7)
    min_data_quality_score: float = Field(default=0.60, ge=0, le=1, allow_inf_nan=False)
    catastrophic_stress_loss: float = Field(default=0.25, gt=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def ranges(self) -> RiskConstraintSet:
        if self.max_cvar_95 < self.max_var_95:
            raise ValueError("CVaR limit cannot be below VaR limit")
        if self.max_cvar_99 < self.max_var_99:
            raise ValueError("99% CVaR limit cannot be below 99% VaR limit")
        return self


class RiskAssessment(_ResearchOnly):
    assessment_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    portfolio_id: str = Field(min_length=1)
    as_of: UtcDatetime
    metrics: tuple[RiskMetric, ...]
    breaches: tuple[RiskBreach, ...]
    stress_results: tuple[StressResult, ...]
    overall_risk_score: float = Field(ge=0, le=100, allow_inf_nan=False)
    highest_severity: RiskSeverity
    data_quality_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    warnings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    provenance: dict[str, Any]
    trace: AnalysisTrace

    @field_validator("as_of")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        result = _require_aware_utc(value)
        if result > datetime.now(UTC):
            raise ValueError("future assessment timestamps are forbidden")
        return result


class RiskDecision(_ResearchOnly):
    decision_id: str = Field(min_length=1)
    assessment_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    decision: RiskDecisionType
    rationale: tuple[str, ...]
    required_modifications: tuple[RiskModification, ...]
    blocking_breaches: tuple[str, ...]
    warnings: tuple[str, ...] = ()
