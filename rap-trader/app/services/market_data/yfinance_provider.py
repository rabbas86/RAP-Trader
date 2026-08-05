import logging
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import yfinance as yf  # type: ignore[import-untyped]

from app.domain.models.market_data import HistoricalBarsRequest, HistoricalBarsResult, MarketDataError, OHLCVBar
from app.services.market_data.base import MarketDataProvider
from app.services.market_data.cache import AbstractCache, InMemoryCache

logger = logging.getLogger(__name__)
YFINANCE_INTERVALS = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "60m", "1d": "1d", "1w": "1wk"}


class YFinanceMarketDataProvider(MarketDataProvider):
    def __init__(
        self,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        cache: AbstractCache[HistoricalBarsResult] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds, max_retries=max_retries)
        self.cache = cache or InMemoryCache(ttl_seconds=300.0)
        self._sleeper = sleeper

    @staticmethod
    def _cache_key(request: HistoricalBarsRequest) -> str:
        return request.model_dump_json()

    @staticmethod
    def _row_value(row: Any, name: str) -> float:
        try:
            value = row[name]
        except KeyError:
            matches = [column for column in row.index if isinstance(column, tuple) and column[0] == name]
            if len(matches) != 1:
                raise MarketDataError(f"provider response is missing {name}") from None
            value = row[matches[0]]
        number = float(value)
        if not math.isfinite(number):
            raise MarketDataError(f"provider response contains invalid {name}")
        return number

    def _download(self, request: HistoricalBarsRequest) -> Any:
        return yf.download(
            tickers=str(request.symbol),
            start=request.start,
            end=request.end,
            interval=YFINANCE_INTERVALS[request.timeframe],
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=self.timeout_seconds,
        )

    def get_bars(self, request: HistoricalBarsRequest) -> HistoricalBarsResult:
        key = self._cache_key(request)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        data: Any = None
        for attempt in range(self.max_retries + 1):
            try:
                data = self._download(request)
                if data is None or data.empty:
                    raise MarketDataError(f"no market data returned for {request.symbol}")
                break
            except Exception as exc:
                error = exc if isinstance(exc, MarketDataError) else MarketDataError(f"market data request failed: {exc}")
                if attempt == self.max_retries:
                    logger.warning("market data request failed", extra={"service": "yfinance", "event": "get_bars", "result": "error"})
                    raise error from exc
                self._sleeper(float(2**attempt))

        bars: list[OHLCVBar] = []
        try:
            for index, row in data.iterrows():
                timestamp = index.to_pydatetime() if hasattr(index, "to_pydatetime") else index
                if not isinstance(timestamp, datetime):
                    raise MarketDataError("provider response contains an invalid timestamp")
                volume = self._row_value(row, "Volume")
                if volume < 0 or not volume.is_integer():
                    raise MarketDataError("provider response contains invalid Volume")
                bars.append(
                    OHLCVBar(
                        timestamp=timestamp,
                        open=self._row_value(row, "Open"),
                        high=self._row_value(row, "High"),
                        low=self._row_value(row, "Low"),
                        close=self._row_value(row, "Close"),
                        volume=int(volume),
                    )
                )
        except MarketDataError:
            raise
        except Exception as exc:
            raise MarketDataError(f"invalid market data response: {exc}") from exc

        bars.sort(key=lambda bar: bar.timestamp)
        if request.limit is not None:
            bars = bars[-request.limit :]
        result = HistoricalBarsResult(
            symbol=request.symbol,
            timeframe=request.timeframe,
            bars=bars,
            provider="yfinance",
            fetched_at=datetime.now(UTC),
        )
        self.cache.set(key, result)
        return result

    def health(self) -> bool:
        return True

    def supported_timeframes(self) -> list[str]:
        return list(YFINANCE_INTERVALS)
