from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from app.domain.models import HistoricalBarsRequest, MarketDataError, MarketDataErrorCode, Symbol
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
    with pytest.raises(MarketDataError) as caught:
        YFinanceMarketDataProvider(max_retries=0).get_bars(request())
    assert caught.value.code is MarketDataErrorCode.PROVIDER_UNAVAILABLE
    assert "network internals" not in caught.value.safe_message


@patch("app.services.market_data.yfinance_provider.yf.download")
def test_yfinance_uses_cache(download: Mock) -> None:
    download.return_value = frame()
    provider = YFinanceMarketDataProvider()
    provider.get_bars(request())
    provider.get_bars(request())
    download.assert_called_once()


@patch("app.services.market_data.yfinance_provider.yf.download")
def test_adjustment_session_and_symbol_are_mapped(download: Mock) -> None:
    download.return_value = frame()
    query = request().model_copy(update={"symbol": Symbol("BRK.B"), "adjustment": "split_adjusted", "session": "extended"})
    YFinanceMarketDataProvider().get_bars(query)
    assert download.call_args.kwargs["tickers"] == "BRK-B"
    assert download.call_args.kwargs["auto_adjust"] is True
    assert download.call_args.kwargs["prepost"] is True


@patch("app.services.market_data.yfinance_provider.yf.download")
def test_empty_response_is_no_data(download: Mock) -> None:
    download.return_value = pd.DataFrame()
    with pytest.raises(MarketDataError) as caught:
        YFinanceMarketDataProvider().get_bars(request())
    assert caught.value.code is MarketDataErrorCode.NO_DATA


@patch("app.services.market_data.yfinance_provider.yf.download")
def test_malformed_rows_are_filtered_and_mark_result_partial(download: Mock) -> None:
    data = frame()
    data.loc[data.index[0], "Open"] = float("nan")
    download.return_value = data.iloc[::-1]
    result = YFinanceMarketDataProvider().get_bars(request())
    assert len(result.bars) == 1
    assert result.partial is True


@pytest.mark.parametrize("volume", [-1, 1.5, "bad"])
@patch("app.services.market_data.yfinance_provider.yf.download")
def test_fully_invalid_volume_is_no_data(download: Mock, volume: object) -> None:
    data = frame()
    data["Volume"] = volume
    download.return_value = data
    with pytest.raises(MarketDataError) as caught:
        YFinanceMarketDataProvider().get_bars(request())
    assert caught.value.code is MarketDataErrorCode.NO_DATA


@patch("app.services.market_data.yfinance_provider.yf.download")
def test_multiindex_columns_and_duplicate_timestamps(download: Mock) -> None:
    data = frame()
    data.columns = pd.MultiIndex.from_tuples([(name, "AAPL") for name in data.columns])
    data = pd.concat([data.iloc[[1]], data.iloc[[0]], data.iloc[[0]]])
    download.return_value = data
    result = YFinanceMarketDataProvider().get_bars(request())
    assert len(result.bars) == 2
    assert result.partial is True
    assert result.bars[0].timestamp < result.bars[1].timestamp


@patch("app.services.market_data.yfinance_provider.yf.download")
def test_naive_provider_timestamps_are_localized(download: Mock) -> None:
    download.return_value = frame().tz_convert(None)
    result = YFinanceMarketDataProvider(exchange_timezone="America/New_York").get_bars(request())
    assert result.bars[0].timestamp == datetime(2026, 1, 1, 5, tzinfo=UTC)


@patch("app.services.market_data.yfinance_provider.yf.download")
def test_ambiguous_dst_timestamp_is_rejected(download: Mock) -> None:
    data = frame().iloc[[0]].copy()
    data.index = pd.DatetimeIndex(["2025-11-02 01:30:00"])
    download.return_value = data
    with pytest.raises(MarketDataError) as caught:
        YFinanceMarketDataProvider(exchange_timezone="America/New_York").get_bars(request())
    assert caught.value.code is MarketDataErrorCode.TIMEZONE_AMBIGUOUS


@patch("app.services.market_data.yfinance_provider.yf.download")
def test_timeout_is_bounded_and_classified(download: Mock) -> None:
    download.side_effect = TimeoutError("private timeout detail")
    sleeper = Mock()
    with pytest.raises(MarketDataError) as caught:
        YFinanceMarketDataProvider(max_retries=2, sleeper=sleeper).get_bars(request())
    assert download.call_count == 3
    assert sleeper.call_count == 2
    assert caught.value.code is MarketDataErrorCode.TIMEOUT
