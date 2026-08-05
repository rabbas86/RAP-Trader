from datetime import UTC, datetime

import pytest

from app.domain.models import HistoricalBarsRequest, MarketDataError, Symbol
from app.services.market_data import MockMarketDataProvider


def request(symbol: str = "AAPL", limit: int | None = None) -> HistoricalBarsRequest:
    return HistoricalBarsRequest(
        symbol=Symbol(symbol), timeframe="1d", start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 6, tzinfo=UTC), limit=limit
    )


def test_mock_is_deterministic_across_instances() -> None:
    assert MockMarketDataProvider().get_bars(request()) == MockMarketDataProvider().get_bars(request())


def test_mock_generates_valid_chronological_bars_and_honors_limit() -> None:
    result = MockMarketDataProvider().get_bars(request(limit=2))
    assert len(result.bars) == 2
    assert result.bars[0].timestamp < result.bars[1].timestamp
    assert result.provider == "mock"


def test_mock_rejects_unsupported_symbol() -> None:
    with pytest.raises(MarketDataError, match="unsupported symbol"):
        MockMarketDataProvider().get_bars(request("NVDA"))


def test_mock_health_and_timeframes() -> None:
    provider = MockMarketDataProvider()
    assert provider.health() is True
    assert provider.supported_timeframes() == ["1m", "5m", "15m", "1h", "1d", "1w"]
