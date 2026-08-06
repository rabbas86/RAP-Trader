from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.models.market_data import Timeframe, UtcDatetime, _require_aware_utc


class KronosModelId(StrEnum):
    MOCK = "mock-kronos-v0"
    MINI = "kronos-mini"
    SMALL = "kronos-small"
    BASE = "kronos-base"


SMA_BASELINE_MODEL_ID = "sma-baseline-v1"
_OFFICIAL_MODELS: frozenset[str] = frozenset({KronosModelId.MINI.value, KronosModelId.SMALL.value, KronosModelId.BASE.value})


def validate_model_id(value: str) -> str:
    """Validate that the model id is supported; reject kronos-large."""
    normalized = value.strip()
    if normalized == "kronos-large":
        raise ValueError("kronos-large is not supported")
    try:
        return KronosModelId(normalized).value
    except ValueError:
        if normalized == SMA_BASELINE_MODEL_ID:
            return normalized
        raise ValueError(f"Unsupported Kronos model id: {normalized}")


def is_official_kronos(model_id: str) -> bool:
    """Return True only for official Kronos model identifiers (not mock or baseline)."""
    return model_id in _OFFICIAL_MODELS


class KronosErrorCodes(StrEnum):
    UNSUPPORTED_MODEL = "UNSUPPORTED_MODEL"
    UNSUPPORTED_MODEL_ID = "UNSUPPORTED_MODEL_ID"
    MODEL_LOAD_FAILED = "MODEL_LOAD_FAILED"
    INFERENCE_FAILED = "INFERENCE_FAILED"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    INVALID_REQUEST = "INVALID_REQUEST"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    MALFORMED_FORECAST = "MALFORMED_FORECAST"
    INPUT_TOO_LONG = "INPUT_TOO_LONG"
    IRREGULAR_SPACING = "IRREGULAR_SPACING"


class KronosError(Exception):
    """Stable public error for Kronos forecast failures."""

    def __init__(
        self,
        code: KronosErrorCodes | str,
        safe_message: str,
        provider: str,
        retryable: bool = False,
        internal_detail: str | None = None,
    ) -> None:
        self.code = KronosErrorCodes(code)
        self.safe_message = safe_message
        self.provider = provider
        self.retryable = retryable
        self.internal_detail = internal_detail
        super().__init__(safe_message)


class ForecastBar(BaseModel):
    """A single future OHLCV bar from a Kronos forecast."""

    model_config = ConfigDict(strict=True)

    timestamp: UtcDatetime
    open: float = Field(gt=0, allow_inf_nan=False)
    high: float = Field(gt=0, allow_inf_nan=False)
    low: float = Field(gt=0, allow_inf_nan=False)
    close: float = Field(gt=0, allow_inf_nan=False)
    volume: float = Field(ge=0)

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: Any) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def validate_price_relationships(self) -> "ForecastBar":
        if self.high < self.low or self.high < self.open or self.high < self.close:
            raise ValueError("high must be greater than or equal to low, open, and close")
        if self.low > self.open or self.low > self.close:
            raise ValueError("low must be less than or equal to open and close")
        return self


class KronosModelMetadata(BaseModel):
    """Metadata describing a supported Kronos model."""

    model_config = ConfigDict(strict=True)

    model_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    context_length: int = Field(gt=0)
    description: str


class KronosForecastRequest(BaseModel):
    """A request for a Kronos forecast over a symbol and historical context."""

    model_config = ConfigDict(strict=True)

    ticker: str = Field(min_length=1, max_length=10)
    model_id: str = Field(min_length=1)
    timeframe: Timeframe
    start: UtcDatetime
    end: UtcDatetime
    lookback: int = Field(default=60, gt=0, le=10000)
    horizon: int = Field(default=5, gt=0, le=100)
    max_sample_count: int | None = Field(default=None, gt=0)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("start", "end", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: Any) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def validate_range(self) -> "KronosForecastRequest":
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class KronosForecast(BaseModel):
    """A Kronos forecast containing future OHLCV bars plus provenance."""

    model_config = ConfigDict(strict=True)

    ticker: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    timeframe: Timeframe
    bars: list[ForecastBar]
    requested_start: UtcDatetime
    requested_end: UtcDatetime
    actual_start: UtcDatetime
    actual_end: UtcDatetime
    lookback_bars: int = Field(ge=0)
    horizon: int = Field(gt=0)
    generated_at: UtcDatetime
    suitable_for_live_trading: bool = False
    warning: str | None = None

    @field_validator("requested_start", "requested_end", "actual_start", "actual_end", "generated_at", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: Any) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def validate_forecast(self) -> "KronosForecast":
        if not self.bars:
            raise ValueError("forecast must contain at least one bar")
        timestamps = [bar.timestamp for bar in self.bars]
        if timestamps != sorted(timestamps):
            raise ValueError("forecast bars must be in chronological order")
        if len(timestamps) != len(set(timestamps)):
            raise ValueError("forecast bars must not contain duplicate timestamps")
        if self.actual_start != timestamps[0] or self.actual_end != timestamps[-1]:
            raise ValueError("actual range must match bar timestamps")
        return self


class KronosForecastMetrics(BaseModel):
    """Deterministic metrics derived from a completed KronosForecast."""

    model_config = ConfigDict(strict=True)

    expected_return: float
    volatility: float = Field(ge=0)
    max_drawdown: float = Field(ge=0)
    upward_bar_ratio: float = Field(ge=0, le=1)
    first_close: float = Field(gt=0)
    final_close: float
    max_high: float = Field(gt=0)
    min_low: float = Field(gt=0)
    direction: Literal["UP", "DOWN", "FLAT"] = "FLAT"
    direction_confidence: float = Field(ge=0, le=1)
    model_version: str = Field(min_length=1)


class KronosProviderHealth(BaseModel):
    """Health snapshot for a Kronos provider."""

    model_config = ConfigDict(strict=True)

    provider: str = Field(min_length=1)
    configured: bool
    reachable: bool | None
    checked_at: UtcDatetime
    status: str = Field(min_length=1)
    detail: str
    model_id: str | None = None

    @field_validator("checked_at", mode="before")
    @classmethod
    def normalize_checked_at(cls, value: Any) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return _require_aware_utc(value)
