from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from app.domain.models import HistoricalBarsRequest, MarketDataError, Symbol
from app.services.market_data import YFinanceMarketDataProvider


def request(limit: int | None = None) -> HistoricalBarsRequest:
    return HistoricalBarsRequest(
        symbol=Symbol("AAPL"), timeframe="1d", start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 3, tzinfo=UTC), limit=limit
    )


def frame() -> pd.DataFrame:
    return pd.DataFrame(
        {"Open": [10.0, 11.0], "High": [12.0, 13.0], "Low": [9.0, 10.0], "Close": [11.0, 12.0], "Volume": [100, 200]},
        index=pd.to_datetime(["2026-01-01", "2026-01-02"], utc=True),
    )


@patch("app.services.market_data.yfinance_provider.yf.download")
def test_yfinance_normalizes_response_and_passes_timeout(download: Mock) -> None:
    download.return_value = frame()
    result = YFinanceMarketDataProvider(timeout_seconds=7).get_bars(request(limit=1))
    assert result.provider == "yfinance"
    assert len(result.bars) == 1
    assert result.bars[0].timestamp.tzinfo is UTC
    assert download.call_args.kwargs["timeout"] == 7
    assert download.call_args.kwargs["interval"] == "1d"


@patch("app.services.market_data.yfinance_provider.yf.download")
def test_yfinance_retries_with_exponential_backoff(download: Mock) -> None:
    download.side_effect = [RuntimeError("temporary"), RuntimeError("temporary"), frame()]
    sleeper = Mock()
    result = YFinanceMarketDataProvider(max_retries=2, sleeper=sleeper).get_bars(request())
    assert len(result.bars) == 2
    assert sleeper.call_args_list[0].args == (1.0,)
    assert sleeper.call_args_list[1].args == (2.0,)


@patch("app.services.market_data.yfinance_provider.yf.download")
def test_yfinance_translates_errors(download: Mock) -> None:
    download.side_effect = RuntimeError("network internals")
    with pytest.raises(MarketDataError, match="market data request failed"):
        YFinanceMarketDataProvider(max_retries=0).get_bars(request())


@patch("app.services.market_data.yfinance_provider.yf.download")
def test_yfinance_uses_cache(download: Mock) -> None:
    download.return_value = frame()
    provider = YFinanceMarketDataProvider()
    provider.get_bars(request())
    provider.get_bars(request())
    download.assert_called_once()
