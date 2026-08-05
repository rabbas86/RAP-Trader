import hashlib
import random
from datetime import UTC, datetime, timedelta

from app.domain.models.market_data import HistoricalBarsRequest, HistoricalBarsResult, MarketDataError, OHLCVBar
from app.services.market_data.base import MarketDataProvider
from app.services.market_data.cache import AbstractCache, InMemoryCache

SUPPORTED_SYMBOLS = frozenset({"AAPL", "MSFT", "GOOG", "TSLA", "SPY"})
TIMEFRAME_DELTAS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}


class MockMarketDataProvider(MarketDataProvider):
    def __init__(self, cache: AbstractCache[HistoricalBarsResult] | None = None) -> None:
        super().__init__(timeout_seconds=1.0, max_retries=0)
        self.cache = cache or InMemoryCache(ttl_seconds=300.0)

    @staticmethod
    def _cache_key(request: HistoricalBarsRequest) -> str:
        return request.model_dump_json()

    def get_bars(self, request: HistoricalBarsRequest) -> HistoricalBarsResult:
        symbol = str(request.symbol)
        if symbol not in SUPPORTED_SYMBOLS:
            raise MarketDataError(f"unsupported symbol: {symbol}")
        key = self._cache_key(request)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        seed_material = f"{symbol}|{request.timeframe}|{request.start.isoformat()}|{request.end.isoformat()}|{request.limit}"
        seed = int.from_bytes(hashlib.sha256(seed_material.encode()).digest()[:8], "big")
        rng = random.Random(seed)
        step = TIMEFRAME_DELTAS[request.timeframe]
        timestamps: list[datetime] = []
        current = request.start
        while current < request.end:
            timestamps.append(current)
            current += step
        if request.limit is not None:
            timestamps = timestamps[-request.limit :]

        price = 50.0 + (seed % 25_000) / 100
        bars: list[OHLCVBar] = []
        for timestamp in timestamps:
            open_price = price
            close_price = max(0.01, open_price * (1 + rng.uniform(-0.01, 0.01)))
            high = max(open_price, close_price) * (1 + rng.uniform(0, 0.005))
            low = min(open_price, close_price) * (1 - rng.uniform(0, 0.005))
            bars.append(
                OHLCVBar(
                    timestamp=timestamp,
                    open=round(open_price, 4),
                    high=round(high, 4),
                    low=round(low, 4),
                    close=round(close_price, 4),
                    volume=rng.randint(1_000, 1_000_000),
                )
            )
            price = close_price
        result = HistoricalBarsResult(
            symbol=request.symbol,
            timeframe=request.timeframe,
            bars=bars,
            provider="mock",
            fetched_at=datetime.fromtimestamp(seed % 2_000_000_000, tz=UTC),
        )
        self.cache.set(key, result)
        return result

    def health(self) -> bool:
        return True

    def supported_timeframes(self) -> list[str]:
        return list(TIMEFRAME_DELTAS)
