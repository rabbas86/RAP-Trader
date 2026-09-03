"""Validation and normalization helpers for Phase 17A forward observations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.domain.models.market_data import Timeframe
from app.services.forward_data.errors import (
    DuplicateConflictError,
    InvalidEventIntervalError,
    InvalidOHLCError,
    InvalidSourceError,
    NaiveTimestampError,
    NegativeVolumeError,
    UnsupportedObservationTypeError,
    UnsupportedTimeframeError,
)


def validate_source(source: Any) -> None:
    if not hasattr(source, "source_id") or not hasattr(source, "environment"):
        raise InvalidSourceError("Forward data source is missing required identity fields.")


def validate_timestamp(value: object, field_name: str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise NaiveTimestampError(f"{field_name} must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise NaiveTimestampError(f"{field_name} must include timezone information.")
    return value.astimezone(UTC)


def validate_event_interval(*, interval_start: datetime, interval_end: datetime, event_time: datetime) -> None:
    if interval_start >= interval_end:
        raise InvalidEventIntervalError("interval_start must be before interval_end.")
    if event_time < interval_start or event_time > interval_end:
        raise InvalidEventIntervalError("event_time must be within the observation interval.")


def validate_market_bar_prices(
    *,
    open_: float | None,
    high: float | None,
    low: float | None,
    close: float | None,
    volume: int | None,
) -> None:
    prices = {"open": open_, "high": high, "low": low, "close": close}
    missing = [name for name, value in prices.items() if value is None]
    if missing:
        raise InvalidOHLCError(f"market_bar observation is missing required fields: {', '.join(missing)}")
    if volume is None:
        raise InvalidOHLCError("market_bar observation is missing required fields: volume")
    open_float = float(open_)
    high_float = float(high)
    low_float = float(low)
    close_float = float(close)
    volume_int = int(volume)
    if high_float < low_float or high_float < open_float or high_float < close_float:
        raise InvalidOHLCError("high must be greater than or equal to low, open, and close")
    if low_float > open_float or low_float > close_float:
        raise InvalidOHLCError("low must be less than or equal to open and close")
    if volume_int < 0:
        raise NegativeVolumeError("volume must be greater than or equal to 0")


def validate_timeframe(timeframe: str) -> str:
    if timeframe not in getattr(Timeframe, "__args__", ()):
        raise UnsupportedTimeframeError(timeframe)
    return timeframe


def validate_observation_type(observation_type: str) -> str:
    allowed = {"market_bar", "quote", "news"}
    if observation_type not in allowed:
        raise UnsupportedObservationTypeError(observation_type)
    return observation_type


def validate_duplicate_conflict(
    existing_payload_hash: str,
    incoming_payload_hash: str,
    observation_id: str,
) -> None:
    if existing_payload_hash != incoming_payload_hash:
        raise DuplicateConflictError(observation_id=observation_id)


__all__ = [
    "validate_duplicate_conflict",
    "validate_event_interval",
    "validate_market_bar_prices",
    "validate_observation_type",
    "validate_source",
    "validate_timeframe",
    "validate_timestamp",
]
