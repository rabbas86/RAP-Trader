from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.domain.models import HistoricalBarsRequest, HistoricalBarsResult, OHLCVBar, Symbol


def bar(timestamp: datetime | None = None) -> OHLCVBar:
    naive_timestamp = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    return OHLCVBar(timestamp=timestamp or naive_timestamp, open=10.0, high=12.0, low=9.0, close=11.0, volume=100)


def test_symbol_is_normalized_and_serializes_as_string() -> None:
    symbol = Symbol(" aapl ")
    assert str(symbol) == "AAPL"
    assert symbol.model_dump() == "AAPL"


@pytest.mark.parametrize("value", ["", "TOOLONG", "BRK.B", "A-B", "é"])
def test_symbol_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValidationError):
        Symbol(value)


def test_bar_normalizes_naive_timestamp_to_utc() -> None:
    assert bar().timestamp.tzinfo is UTC


def test_bar_converts_aware_timestamp_to_utc() -> None:
    timestamp = datetime.fromisoformat("2026-01-01T03:00:00+03:00")
    assert bar(timestamp).timestamp == datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"high": 8.0}, "high"),
        ({"low": 11.5}, "low"),
        ({"volume": -1}, "volume"),
        ({"open": 0.0}, "open"),
    ],
)
def test_bar_rejects_invalid_values(changes: dict[str, float | int], message: str) -> None:
    values: dict[str, object] = {"timestamp": datetime.now(UTC), "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 100}
    values.update(changes)
    with pytest.raises(ValidationError, match=message):
        OHLCVBar(**values)  # type: ignore[arg-type]


def test_request_requires_chronological_range_and_valid_timeframe() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="start must be before end"):
        HistoricalBarsRequest(symbol=Symbol("AAPL"), timeframe="1d", start=now, end=now)
    with pytest.raises(ValidationError):
        HistoricalBarsRequest(symbol=Symbol("AAPL"), timeframe="2m", start=now, end=now + timedelta(days=1))  # type: ignore[arg-type]


def test_result_rejects_out_of_order_and_duplicate_bars() -> None:
    first = bar(datetime(2026, 1, 1, tzinfo=UTC))
    second = bar(datetime(2026, 1, 2, tzinfo=UTC))
    with pytest.raises(ValidationError, match="chronological"):
        HistoricalBarsResult(symbol=Symbol("AAPL"), timeframe="1d", bars=[second, first], provider="mock", fetched_at=datetime.now(UTC))
    with pytest.raises(ValidationError, match="duplicate"):
        HistoricalBarsResult(symbol=Symbol("AAPL"), timeframe="1d", bars=[first, first], provider="mock", fetched_at=datetime.now(UTC))
