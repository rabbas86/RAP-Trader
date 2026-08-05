from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

Timeframe = Literal["1m", "5m", "15m", "1h", "1d", "1w"]
UtcDatetime = Annotated[datetime, Field()]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class Symbol(RootModel[str]):
    """Normalized US-equity ticker accepted by the market-data boundary."""

    model_config = ConfigDict(strict=True)
    root: str = Field(min_length=1, max_length=5, pattern=r"^[A-Z0-9]{1,5}$")

    @field_validator("root", mode="before")
    @classmethod
    def normalize(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    def __str__(self) -> str:
        return self.root


class OHLCVBar(BaseModel):
    model_config = ConfigDict(strict=True)

    timestamp: UtcDatetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0)

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _as_utc(value)

    @model_validator(mode="after")
    def validate_price_relationships(self) -> "OHLCVBar":
        if self.high < self.low:
            raise ValueError("high must be greater than or equal to low")
        if self.high < self.open or self.high < self.close:
            raise ValueError("high must be greater than or equal to open and close")
        if self.low > self.open or self.low > self.close:
            raise ValueError("low must be less than or equal to open and close")
        return self


class HistoricalBarsRequest(BaseModel):
    model_config = ConfigDict(strict=True)

    symbol: Symbol
    timeframe: Timeframe
    start: UtcDatetime
    end: UtcDatetime
    limit: int | None = Field(default=None, gt=0, le=10_000)

    @field_validator("start", "end")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _as_utc(value)

    @model_validator(mode="after")
    def validate_range(self) -> "HistoricalBarsRequest":
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class HistoricalBarsResult(BaseModel):
    model_config = ConfigDict(strict=True)

    symbol: Symbol
    timeframe: Timeframe
    bars: list[OHLCVBar]
    provider: str = Field(min_length=1)
    fetched_at: UtcDatetime

    @field_validator("fetched_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _as_utc(value)

    @model_validator(mode="after")
    def validate_bar_timestamps(self) -> "HistoricalBarsResult":
        timestamps = [bar.timestamp for bar in self.bars]
        if timestamps != sorted(timestamps):
            raise ValueError("bars must be in chronological order")
        if len(timestamps) != len(set(timestamps)):
            raise ValueError("bars must not contain duplicate timestamps")
        return self


class MarketDataError(Exception):
    """Stable error exposed by all market-data implementations."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)
