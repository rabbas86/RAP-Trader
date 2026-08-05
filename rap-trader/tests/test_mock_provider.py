from datetime import UTC, datetime

import pytest

from app.domain.models import HistoricalBarsRequest, MarketDataError, MarketDataErrorCode, Symbol
from app.services.market_data import InMemoryCache, MockMarketDataProvider, YFinanceMarketDataProvider


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
    with pytest.raises(MarketDataError) as caught:
        MockMarketDataProvider().get_bars(request("NVDA"))
    assert caught.value.code is MarketDataErrorCode.UNSUPPORTED_SYMBOL


def test_mock_health_and_timeframes() -> None:
    provider = MockMarketDataProvider()
    assert provider.health().reachable is True
    assert provider.supported_timeframes() == ["1m", "5m", "15m", "1h", "1d", "1w"]


def test_mock_multi_year_minute_request_only_generates_limit() -> None:
    large = HistoricalBarsRequest(
        symbol=Symbol("AAPL"),
        timeframe="1m",
        start=datetime(2022, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 1, tzinfo=UTC),
        limit=3,
    )
    result = MockMarketDataProvider().get_bars(large)
    assert len(result.bars) == 3
    assert result.bars[0].timestamp == large.end - 3 * (large.end - result.bars[-1].timestamp)


def test_mock_enforces_configured_max_limit() -> None:
    with pytest.raises(MarketDataError) as caught:
        MockMarketDataProvider(max_limit=2).get_bars(request(limit=3))
    assert caught.value.code is MarketDataErrorCode.REQUEST_TOO_LARGE


def test_shared_cache_is_isolated_by_provider() -> None:
    cache = InMemoryCache(ttl_seconds=30)
    mock = MockMarketDataProvider(cache=cache)
    yahoo = YFinanceMarketDataProvider(cache=cache)
    mock_result = mock.get_bars(request(limit=1))
    assert yahoo._cache_key(request(limit=1)) != mock._cache_key(request(limit=1))
    assert cache.get(yahoo._cache_key(request(limit=1))) is None
    assert mock_result.provider == "mock"


def test_adjustment_policies_do_not_share_cache_entries() -> None:
    provider = MockMarketDataProvider()
    raw = request(limit=1)
    adjusted = raw.model_copy(update={"adjustment": "split_adjusted"})
    assert provider._cache_key(raw) != provider._cache_key(adjusted)
