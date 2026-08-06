import hashlib
import random
from datetime import UTC, datetime, timedelta

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

SUPPORTED_SYMBOLS = frozenset({"AAPL", "MSFT", "GOOG", "TSLA", "SPY", "BRK.B", "BF.B"})
MAX_LIMIT = 5000
TIMEFRAME_DELTAS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}
MAX_RANGES = {
    "1m": timedelta(days=365 * 5),
    "5m": timedelta(days=365 * 5),
    "15m": timedelta(days=365 * 5),
    "1h": timedelta(days=365 * 5),
    "1d": timedelta(days=365 * 10),
    "1w": timedelta(days=365 * 10),
}


class MockMarketDataProvider(MarketDataProvider):
    """Deterministic synthetic data; timestamps are not exchange-calendar accurate."""

    MAX_LIMIT = MAX_LIMIT

    def __init__(self, cache: AbstractCache[HistoricalBarsResult] | None = None, max_limit: int = MAX_LIMIT) -> None:
        super().__init__(timeout_seconds=1.0, max_retries=0)
        if max_limit <= 0:
            raise ValueError("max_limit must be positive")
        self.cache = cache if cache is not None else InMemoryCache(ttl_seconds=300.0)
        self.max_limit = max_limit

    def _error(self, code: MarketDataErrorCode, message: str) -> MarketDataError:
        return MarketDataError(code, message, "mock")

    def _cache_key(self, request: HistoricalBarsRequest) -> str:
        return cache_key_builder(
            "mock",
            request,
            request.adjustment,
            request.session,
            {"max_limit": self.max_limit, "version": 2},
        )

    def get_bars(self, request: HistoricalBarsRequest) -> HistoricalBarsResult:
        symbol = str(request.symbol)
        if symbol not in SUPPORTED_SYMBOLS:
            raise self._error(MarketDataErrorCode.UNSUPPORTED_SYMBOL, f"Symbol {symbol} is not supported")
        if request.adjustment == "total_return_adjusted":
            raise self._error(MarketDataErrorCode.ADJUSTMENT_UNSUPPORTED, "The requested adjustment policy is not supported")
        if request.limit is not None and request.limit > self.max_limit:
            raise self._error(MarketDataErrorCode.REQUEST_TOO_LARGE, f"Request limit exceeds the maximum of {self.max_limit}")
        max_range = MAX_RANGES.get(request.timeframe)
        if max_range is not None and request.end - request.start > max_range:
            raise self._error(MarketDataErrorCode.REQUEST_TOO_LARGE, "Requested date range exceeds the provider policy")

        key = self._cache_key(request)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        step = TIMEFRAME_DELTAS[request.timeframe]
        limit = request.limit if request.limit is not None else self.max_limit
        effective_start = max(request.start, request.end - step * limit)
        seed_material = (
            f"{symbol}|{request.timeframe}|{request.start.isoformat()}|{request.end.isoformat()}|"
            f"{request.limit}|{request.adjustment}|{request.session}"
        )
        seed = int.from_bytes(hashlib.sha256(seed_material.encode()).digest()[:8], "big")
        rng = random.Random(seed)
        price = 50.0 + (seed % 25_000) / 100
        bars: list[OHLCVBar] = []
        current = effective_start
        while current < request.end and len(bars) < limit:
            open_price = price
            close_price = max(0.01, open_price * (1 + rng.uniform(-0.01, 0.01)))
            high = max(open_price, close_price) * (1 + rng.uniform(0, 0.005))
            low = min(open_price, close_price) * (1 - rng.uniform(0, 0.005))
            bars.append(
                OHLCVBar(
                    timestamp=current,
                    open=round(open_price, 4),
                    high=round(high, 4),
                    low=round(low, 4),
                    close=round(close_price, 4),
                    volume=rng.randint(1_000, 1_000_000),
                )
            )
            price = close_price
            current += step
        if not bars:
            raise self._error(MarketDataErrorCode.NO_DATA, "No market data matched the request")
        result = HistoricalBarsResult(
            symbol=request.symbol,
            timeframe=request.timeframe,
            bars=bars,
            provider="mock",
            requested_start=request.start,
            requested_end=request.end,
            actual_start=bars[0].timestamp,
            actual_end=bars[-1].timestamp,
            adjustment=request.adjustment,
            session=request.session,
            currency="USD",
            exchange="SYNTHETIC",
            partial=effective_start > request.start,
            retrieved_at=datetime.fromtimestamp(seed % 2_000_000_000, tz=UTC),
        )
        self.cache.set(key, result)
        return result

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider="mock",
            configured=True,
            reachable=True,
            checked_at=datetime.now(UTC),
            status="healthy",
            detail="Deterministic synthetic provider is available",
        )

    def supported_timeframes(self) -> list[str]:
        return list(TIMEFRAME_DELTAS)
