from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.domain.models import HistoricalBarsRequest, HistoricalBarsResult, OHLCVBar, ProviderHealth, Symbol


def bar(timestamp: datetime | None = None) -> OHLCVBar:
    return OHLCVBar(timestamp=timestamp or datetime(2026, 1, 1, tzinfo=UTC), open=10.0, high=12.0, low=9.0, close=11.0, volume=100)


@pytest.mark.parametrize("value", [" aapl ", "BRK.B", "BF.B", "ABC123.X"])
def test_symbol_is_normalized_and_accepts_class_shares(value: str) -> None:
    symbol = Symbol(value)
    assert str(symbol) == value.strip().upper()


@pytest.mark.parametrize("value", ["", "TOOLONG1234", "A-B", "A/B", "é", ".ABC", "ABC."])
def test_symbol_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValidationError):
        Symbol(value)


def test_symbol_maps_provider_format() -> None:
    assert Symbol("BRK.B").to_provider("yfinance") == "BRK-B"
    assert Symbol("BRK.B").to_provider("mock") == "BRK.B"


def test_naive_domain_timestamps_are_rejected() -> None:
    naive = datetime(2026, 1, 1)  # noqa: DTZ001
    with pytest.raises(ValidationError, match="timezone"):
        bar(naive)
    with pytest.raises(ValidationError, match="timezone"):
        HistoricalBarsRequest(symbol=Symbol("AAPL"), timeframe="1d", start=naive, end=naive + timedelta(days=1))


def test_aware_timestamps_are_converted_to_utc() -> None:
    source = datetime(2026, 1, 1, 3, tzinfo=timezone(timedelta(hours=3)))
    assert bar(source).timestamp == datetime(2026, 1, 1, tzinfo=UTC)


def test_request_defaults_to_raw_regular_and_validates_range() -> None:
    now = datetime.now(UTC)
    request = HistoricalBarsRequest(symbol=Symbol("AAPL"), timeframe="1d", start=now, end=now + timedelta(days=1))
    assert request.adjustment == "raw"
    assert request.session == "regular"
    with pytest.raises(ValidationError, match="start must be before end"):
        HistoricalBarsRequest(symbol=Symbol("AAPL"), timeframe="1d", start=now, end=now)


def result(bars: list[OHLCVBar]) -> HistoricalBarsResult:
    return HistoricalBarsResult(
        symbol=Symbol("AAPL"),
        timeframe="1d",
        bars=bars,
        provider="mock",
        requested_start=datetime(2026, 1, 1, tzinfo=UTC),
        requested_end=datetime(2026, 1, 3, tzinfo=UTC),
        actual_start=bars[0].timestamp,
        actual_end=bars[-1].timestamp,
        adjustment="raw",
        session="regular",
        retrieved_at=datetime.now(UTC),
    )


def test_result_rejects_out_of_order_and_duplicate_bars() -> None:
    first = bar(datetime(2026, 1, 1, tzinfo=UTC))
    second = bar(datetime(2026, 1, 2, tzinfo=UTC))
    with pytest.raises(ValidationError, match="chronological"):
        result([second, first])
    with pytest.raises(ValidationError, match="duplicate"):
        result([first, first])


def test_provider_health_normalizes_checked_at() -> None:
    health = ProviderHealth(
        provider="mock",
        configured=True,
        reachable=True,
        checked_at=datetime(2026, 1, 1, 3, tzinfo=timezone(timedelta(hours=3))),
        status="healthy",
        detail="ok",
    )
    assert health.checked_at == datetime(2026, 1, 1, tzinfo=UTC)
