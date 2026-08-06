"""Strict, research-only technical-analysis result models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.models.analyst import EvidenceStrength
from app.domain.models.market_data import Timeframe, UtcDatetime, _require_aware_utc


class _TechnicalModel(BaseModel):
    model_config = ConfigDict(strict=True)


class TechnicalIndicatorValue(_TechnicalModel):
    name: str = Field(min_length=1)
    value: float = Field(allow_inf_nan=False)
    period: int | None = Field(default=None, gt=0)
    direction: Literal["up", "down", "flat"]
    timestamp: UtcDatetime

    @field_validator("timestamp")
    @classmethod
    def timestamp_utc(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)


class TechnicalLevel(_TechnicalModel):
    price: float = Field(gt=0, allow_inf_nan=False)
    level_type: Literal["support", "resistance"]
    strength: EvidenceStrength
    confirmed_at: UtcDatetime
    touch_count: int = Field(gt=0)
    broken: bool

    @field_validator("confirmed_at")
    @classmethod
    def timestamp_utc(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)


class SwingPoint(_TechnicalModel):
    timestamp: UtcDatetime
    price: float = Field(gt=0, allow_inf_nan=False)
    type: Literal["high", "low"]
    confirmed_at: UtcDatetime
    strength: EvidenceStrength
    bar_index: int = Field(ge=0)

    @field_validator("timestamp", "confirmed_at")
    @classmethod
    def timestamp_utc(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)


class MarketStructureState(_TechnicalModel):
    regime: Literal["uptrend", "downtrend", "range_bound"]
    last_confirmed_timestamp: UtcDatetime | None = None
    higher_highs: int = Field(ge=0)
    higher_lows: int = Field(ge=0)
    lower_highs: int = Field(ge=0)
    lower_lows: int = Field(ge=0)
    bos_timestamp: UtcDatetime | None = None
    choch_timestamp: UtcDatetime | None = None

    @field_validator("last_confirmed_timestamp", "bos_timestamp", "choch_timestamp")
    @classmethod
    def timestamp_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_aware_utc(value)


class TechnicalAnalysisSnapshot(_TechnicalModel):
    bars_analyzed: int = Field(gt=0)
    timeframe: Timeframe
    indicator_values: list[TechnicalIndicatorValue]
    swing_points: list[SwingPoint]
    structure: MarketStructureState
    levels: list[TechnicalLevel]
    generated_at: UtcDatetime

    @field_validator("generated_at")
    @classmethod
    def timestamp_utc(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)
