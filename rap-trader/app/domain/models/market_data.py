from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

Timeframe = Literal["1m", "5m", "15m", "1h", "1d", "1w"]
AdjustmentPolicy = Literal["raw", "split_adjusted", "total_return_adjusted"]
SessionPolicy = Literal["regular", "extended", "all"]
UtcDatetime = Annotated[datetime, Field()]


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include timezone information")
    return value.astimezone(UTC)


class Symbol(RootModel[str]):
    """Normalized equity ticker; adapters translate it at their provider boundary."""

    model_config = ConfigDict(strict=True)
    root: str = Field(min_length=1, max_length=10, pattern=r"^[A-Z0-9]+(?:\.[A-Z0-9]+)*$")

    @field_validator("root", mode="before")
    @classmethod
    def normalize(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    def to_provider(self, provider: str) -> str:
        """Translate canonical class-share notation to a provider ticker."""
        return self.root.replace(".", "-") if provider.lower() == "yfinance" else self.root

    def __str__(self) -> str:
        return self.root


class OHLCVBar(BaseModel):
    model_config = ConfigDict(strict=True)

    timestamp: UtcDatetime
    open: float = Field(gt=0, allow_inf_nan=False)
    high: float = Field(gt=0, allow_inf_nan=False)
    low: float = Field(gt=0, allow_inf_nan=False)
    close: float = Field(gt=0, allow_inf_nan=False)
    volume: int = Field(ge=0)

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def validate_price_relationships(self) -> "OHLCVBar":
        if self.high < self.low or self.high < self.open or self.high < self.close:
            raise ValueError("high must be greater than or equal to low, open, and close")
        if self.low > self.open or self.low > self.close:
            raise ValueError("low must be less than or equal to open and close")
        return self


class HistoricalBarsRequest(BaseModel):
    """A bounded historical query with explicit adjustment and market-session policy.

    ``raw`` preserves reported OHLC, ``split_adjusted`` adjusts for splits, and
    ``total_return_adjusted`` would additionally account for distributions.
    ``regular`` selects the core session, ``extended`` includes pre/post-market,
    and ``all`` requests every session represented by the provider.
    """

    model_config = ConfigDict(strict=True)
    symbol: Symbol
    timeframe: Timeframe
    start: UtcDatetime
    end: UtcDatetime
    limit: int | None = Field(default=None, gt=0, le=100_000)
    adjustment: AdjustmentPolicy = "raw"
    session: SessionPolicy = "regular"

    @field_validator("start", "end")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def validate_range(self) -> "HistoricalBarsRequest":
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class HistoricalBarsResult(BaseModel):
    """Normalized bars plus the complete policy, range, and retrieval provenance."""

    model_config = ConfigDict(strict=True)
    symbol: Symbol
    timeframe: Timeframe
    bars: list[OHLCVBar]
    provider: str = Field(min_length=1)
    requested_start: UtcDatetime
    requested_end: UtcDatetime
    actual_start: UtcDatetime
    actual_end: UtcDatetime
    adjustment: AdjustmentPolicy
    session: SessionPolicy
    currency: str | None = None
    exchange: str | None = None
    partial: bool = False
    retrieved_at: UtcDatetime

    @field_validator("requested_start", "requested_end", "actual_start", "actual_end", "retrieved_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def validate_bar_timestamps(self) -> "HistoricalBarsResult":
        if not self.bars:
            raise ValueError("successful results must contain at least one bar")
        timestamps = [bar.timestamp for bar in self.bars]
        if timestamps != sorted(timestamps):
            raise ValueError("bars must be in chronological order")
        if len(timestamps) != len(set(timestamps)):
            raise ValueError("bars must not contain duplicate timestamps")
        if self.actual_start != timestamps[0] or self.actual_end != timestamps[-1]:
            raise ValueError("actual range must match bar timestamps")
        return self


class MarketDataErrorCode(StrEnum):
    UNSUPPORTED_SYMBOL = "UNSUPPORTED_SYMBOL"
    INVALID_REQUEST = "INVALID_REQUEST"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    NO_DATA = "NO_DATA"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    ADJUSTMENT_UNSUPPORTED = "ADJUSTMENT_UNSUPPORTED"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    TIMEZONE_AMBIGUOUS = "TIMEZONE_AMBIGUOUS"


class MarketDataError(Exception):
    """Stable public error with private diagnostic detail kept out of API output."""

    def __init__(
        self,
        code: MarketDataErrorCode | str,
        safe_message: str,
        provider: str,
        retryable: bool = False,
        internal_detail: str | None = None,
    ) -> None:
        self.code = MarketDataErrorCode(code)
        self.safe_message = safe_message
        self.provider = provider
        self.retryable = retryable
        self.internal_detail = internal_detail
        super().__init__(safe_message)


class ProviderHealth(BaseModel):
    model_config = ConfigDict(strict=True)
    provider: str = Field(min_length=1)
    configured: bool
    reachable: bool | None
    checked_at: UtcDatetime
    status: str = Field(min_length=1)
    detail: str

    @field_validator("checked_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)
