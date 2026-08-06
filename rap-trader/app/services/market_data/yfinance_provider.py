import logging
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

import pandas as pd  # type: ignore[import-untyped]
import yfinance as yf  # type: ignore[import-untyped]
from pydantic import ValidationError

from app.domain.models.market_data import (
    HistoricalBarsRequest,
    HistoricalBarsResult,
    MarketDataError,
    MarketDataErrorCode,
    OHLCVBar,
    ProviderHealth,
)
from app.services.market_data.base import MarketDataProvider
from app.services.market_data.cache import AbstractCache, InMemoryCache, cache_key_builder

logger = logging.getLogger(__name__)
YFINANCE_INTERVALS = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "60m", "1d": "1d", "1w": "1wk"}


class YFinanceMarketDataProvider(MarketDataProvider):
    def __init__(
        self,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        cache: AbstractCache[HistoricalBarsResult] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        exchange_timezone: str = "America/New_York",
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds, max_retries=max_retries)
        self.cache = cache if cache is not None else InMemoryCache(ttl_seconds=300.0)
        self._sleeper = sleeper
        self.exchange_timezone = exchange_timezone

    def _error(self, code: MarketDataErrorCode, message: str, *, retryable: bool = False, detail: str | None = None) -> MarketDataError:
        return MarketDataError(code, message, "yfinance", retryable, detail)

    def _cache_key(self, request: HistoricalBarsRequest) -> str:
        config: dict[str, object] = {
            "interval": YFINANCE_INTERVALS[request.timeframe],
            "exchange_timezone": self.exchange_timezone,
            "version": 2,
        }
        return cache_key_builder("yfinance", request, request.adjustment, request.session, config)

    @staticmethod
    def _row_value(row: Any, name: str) -> float:
        candidates = [column for column in row.index if column == name or (isinstance(column, tuple) and column[0] == name)]
        if len(candidates) != 1:
            raise ValueError(f"missing or ambiguous {name}")
        value = row[candidates[0]]
        if isinstance(value, bool):
            raise TypeError(f"invalid {name}")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"invalid {name}")
        return number

    def _download(self, request: HistoricalBarsRequest) -> Any:
        return yf.download(
            tickers=request.symbol.to_provider("yfinance"),
            start=request.start,
            end=request.end,
            interval=YFINANCE_INTERVALS[request.timeframe],
            auto_adjust=request.adjustment == "split_adjusted",
            prepost=request.session in {"extended", "all"},
            progress=False,
            threads=False,
            timeout=self.timeout_seconds,
        )

    def _timestamp(self, value: object) -> datetime:
        timestamp = pd.Timestamp(value)
        try:
            if timestamp.tzinfo is None:
                timestamp = timestamp.tz_localize(self.exchange_timezone, ambiguous="raise", nonexistent="raise")
            timestamp = timestamp.tz_convert(UTC)
        except (TypeError, ValueError) as exc:
            raise self._error(
                MarketDataErrorCode.TIMEZONE_AMBIGUOUS,
                "Provider timestamp timezone could not be interpreted safely",
                detail=repr(exc),
            ) from exc
        return cast(datetime, timestamp.to_pydatetime())

    def _validate_columns(self, data: Any) -> None:
        expected = {"Open", "High", "Low", "Close", "Volume"}
        names = [column[0] if isinstance(column, tuple) else column for column in data.columns]
        missing = expected.difference(names)
        ambiguous = {name for name in expected if names.count(name) != 1}
        if missing or ambiguous:
            detail = f"missing={sorted(missing)!r}, ambiguous={sorted(ambiguous)!r}"
            raise self._error(
                MarketDataErrorCode.MALFORMED_RESPONSE,
                "Market data provider returned an invalid schema",
                detail=detail,
            )

    def get_bars(self, request: HistoricalBarsRequest) -> HistoricalBarsResult:
        if request.adjustment == "total_return_adjusted":
            raise self._error(
                MarketDataErrorCode.ADJUSTMENT_UNSUPPORTED,
                "The requested adjustment policy is not supported",
            )
        if request.session != "regular" and request.timeframe == "1w":
            raise self._error(MarketDataErrorCode.INVALID_REQUEST, "Extended sessions are not supported for weekly bars")
        key = self._cache_key(request)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        data: Any = None
        for attempt in range(self.max_retries + 1):
            try:
                data = self._download(request)
                break
            except Exception as exc:
                is_timeout = isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()
                code = MarketDataErrorCode.TIMEOUT if is_timeout else MarketDataErrorCode.PROVIDER_UNAVAILABLE
                if attempt == self.max_retries:
                    logger.exception(
                        "yfinance download failed",
                        extra={"service": "yfinance", "event": "get_bars", "result": "error"},
                    )
                    raise self._error(code, "Market data provider request failed", retryable=True, detail=repr(exc)) from exc
                self._sleeper(float(2**attempt))

        if data is None or not hasattr(data, "empty") or data.empty:
            raise self._error(MarketDataErrorCode.NO_DATA, "No market data matched the request")
        self._validate_columns(data)

        bars_by_timestamp: dict[datetime, OHLCVBar] = {}
        malformed_rows = 0
        for index, row in data.iterrows():
            try:
                timestamp = self._timestamp(index)
                volume = self._row_value(row, "Volume")
                if volume < 0 or not volume.is_integer():
                    raise ValueError("invalid Volume")
                bar = OHLCVBar(
                    timestamp=timestamp,
                    open=self._row_value(row, "Open"),
                    high=self._row_value(row, "High"),
                    low=self._row_value(row, "Low"),
                    close=self._row_value(row, "Close"),
                    volume=int(volume),
                )
                if timestamp in bars_by_timestamp:
                    malformed_rows += 1
                bars_by_timestamp[timestamp] = bar
            except MarketDataError:
                raise
            except (TypeError, ValueError, ValidationError):
                malformed_rows += 1

        bars = sorted(bars_by_timestamp.values(), key=lambda bar: bar.timestamp)
        if request.limit is not None:
            bars = bars[-request.limit :]
        if not bars:
            raise self._error(MarketDataErrorCode.NO_DATA, "No valid market data matched the request")
        result = HistoricalBarsResult(
            symbol=request.symbol,
            timeframe=request.timeframe,
            bars=bars,
            provider="yfinance",
            requested_start=request.start,
            requested_end=request.end,
            actual_start=bars[0].timestamp,
            actual_end=bars[-1].timestamp,
            adjustment=request.adjustment,
            session=request.session,
            currency=None,
            exchange=None,
            partial=malformed_rows > 0 or (request.limit is not None and len(data.index) > request.limit),
            retrieved_at=datetime.now(UTC),
        )
        self.cache.set(key, result)
        return result

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider="yfinance",
            configured=True,
            reachable=None,
            checked_at=datetime.now(UTC),
            status="degraded",
            detail="Configured; network reachability was not checked",
        )

    def supported_timeframes(self) -> list[str]:
        return list(YFINANCE_INTERVALS)
