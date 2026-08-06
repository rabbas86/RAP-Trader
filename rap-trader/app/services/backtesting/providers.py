"""Benchmark forecast providers for backtesting.

Implements deterministic, offline benchmark providers that produce future
OHLCV bar forecasts.  These providers implement the
``KronosForecastProvider`` interface so they can be compared alongside
``MockKronosProvider``, ``SMAForecastProvider``, and
``LocalKronosProvider`` (when manually supplied).

Providers implemented:

* ``MockBenchmarkProvider`` — random-walk synthetic forecast.
* ``SMAForecastProvider`` — re-exported from Phase 3 for convenience.
* ``LastValueForecastProvider`` — naively repeats the last close.
* ``DriftForecastProvider`` — extrapolates the mean historical return.

All providers are deterministic: identical inputs always produce identical
outputs.  None of them import or call any broker, execution, order, risk,
or portfolio component.
"""

from __future__ import annotations

from abc import ABC
from datetime import UTC, datetime
from typing import Any, ClassVar  # noqa: F401  (available for subclasses)

from app.domain.models import (
    ForecastBar,
    HistoricalBarsRequest,
    KronosError,
    KronosErrorCodes,
    KronosForecast,
    KronosForecastRequest,
    KronosModelMetadata,
    KronosProviderHealth,
    Symbol,
)
from app.services.backtesting.engine import _timeframe_step
from app.services.kronos import KronosForecastProvider
from app.services.market_data import AbstractCache, InMemoryCache, MarketDataProvider, MockMarketDataProvider

MODEL_LAST_VALUE = "last-value-v1"
MODEL_DRIFT = "drift-v1"
MODEL_MOCK_BENCHMARK = "mock-benchmark-v1"


class BenchmarkForecastProvider(KronosForecastProvider, ABC):
    """Abstract base for benchmark forecast providers.

    Extends the Phase 3 ``KronosForecastProvider`` interface.  Concrete
    benchmark providers must implement ``forecast``.  The ``health`` and
    ``model_metadata`` methods have default implementations.
    """

    LIVE_TRADING_SUITABLE: ClassVar[bool] = False
    MODEL_VERSION: ClassVar[str] = ""
    SUPPORTED_MODELS: ClassVar[list[str]] = []
    CONTEXT_LENGTH: ClassVar[int] = 0

    def __init__(self, cache: AbstractCache[KronosForecast] | None = None) -> None:
        self.cache = cache if cache is not None else InMemoryCache[KronosForecast](ttl_seconds=300.0)

    def supported_models(self) -> list[str]:
        """Return stable public model identifiers."""
        return list(self.SUPPORTED_MODELS)

    def _cache_key(self, request: KronosForecastRequest, model_name: str) -> str:
        """Build a deterministic cache key for a forecast request."""
        import hashlib
        import json

        payload = {
            "provider": model_name,
            "ticker": request.ticker,
            "timeframe": request.timeframe,
            "start": request.start.isoformat(),
            "end": request.end.isoformat(),
            "lookback": request.lookback,
            "horizon": request.horizon,
            "version": 1,
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def health(self) -> KronosProviderHealth:
        return KronosProviderHealth(
            provider=self.__class__.__name__.lower(),
            configured=True,
            reachable=True,
            checked_at=datetime.now(UTC),
            status="healthy",
            detail="Deterministic benchmark provider (offline only)",
            model_id=self.MODEL_VERSION,
        )

    def model_metadata(self, model_id: str) -> KronosModelMetadata:
        if model_id not in self.SUPPORTED_MODELS:
            raise KronosError(
                KronosErrorCodes.UNSUPPORTED_MODEL,
                f"Model {model_id} is not supported by {self.__class__.__name__}",
                "benchmark",
            )
        return KronosModelMetadata(
            model_id=model_id,
            display_name=self.__class__.__name__,
            context_length=10000,
            description="Benchmark forecast provider; not a live-trading model",
        )


class MockBenchmarkProvider(BenchmarkForecastProvider):
    """Deterministic random-walk benchmark.

    Produces future bars by continuing the last close with small random
    perturbations seeded by the request parameters.  This provider is
    intentionally naive and serves as a lower-bound benchmark.
    """

    MODEL_VERSION: ClassVar[str] = MODEL_MOCK_BENCHMARK
    SUPPORTED_MODELS: ClassVar[list[str]] = [MODEL_MOCK_BENCHMARK]
    CONTEXT_LENGTH: ClassVar[int] = 512

    def __init__(self, cache: AbstractCache[KronosForecast] | None = None) -> None:
        super().__init__(cache)
        import random

        self._random_cls = random.Random

    def forecast(self, request: KronosForecastRequest) -> KronosForecast:
        key = self._cache_key(request, "mock-benchmark")
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        seed = _deterministic_seed(request)
        rng = self._random_cls(seed)
        step = _timeframe_step(request.timeframe)
        last_close = 100.0  # default when no market data
        # Try to use the request end as the last price anchor
        bars: list[ForecastBar] = []
        current = request.end + step
        price = last_close
        for _ in range(request.horizon):
            open_price = price
            close_price = max(0.01, open_price * (1 + rng.uniform(-0.02, 0.02)))
            high = max(open_price, close_price) * (1 + rng.uniform(0, 0.01))
            low = min(open_price, close_price) * (1 - rng.uniform(0, 0.01))
            bars.append(
                ForecastBar(
                    timestamp=current,
                    open=round(open_price, 4),
                    high=round(high, 4),
                    low=round(low, 4),
                    close=round(close_price, 4),
                    volume=float(rng.randint(1_000, 1_000_000)),
                )
            )
            price = close_price
            current += step

        result = KronosForecast(
            ticker=request.ticker,
            model_id=request.model_id,
            timeframe=request.timeframe,
            bars=bars,
            requested_start=request.start,
            requested_end=request.end,
            actual_start=bars[0].timestamp,
            actual_end=bars[-1].timestamp,
            lookback_bars=request.lookback,
            horizon=request.horizon,
            generated_at=datetime.now(UTC),
            suitable_for_live_trading=False,
            warning="Mock benchmark forecast; not suitable for live trading",
        )
        self.cache.set(key, result)
        return result


class LastValueForecastProvider(BenchmarkForecastProvider):
    """Naively repeats the last observed close for every future bar.

    This is the simplest possible benchmark — it assumes prices will not
    change.  It is useful as a sanity-check baseline: any model that cannot
    beat the last-value forecast on average is providing no incremental
    information.
    """

    MODEL_VERSION: ClassVar[str] = MODEL_LAST_VALUE
    SUPPORTED_MODELS: ClassVar[list[str]] = [MODEL_LAST_VALUE]
    CONTEXT_LENGTH: ClassVar[int] = 10000

    def __init__(
        self,
        provider: MarketDataProvider | None = None,
        cache: AbstractCache[KronosForecast] | None = None,
    ) -> None:
        super().__init__(cache)
        self.provider = provider if provider is not None else MockMarketDataProvider()

    def forecast(self, request: KronosForecastRequest) -> KronosForecast:
        key = self._cache_key(request, "last-value")
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        step = _timeframe_step(request.timeframe)
        try:
            market_request = HistoricalBarsRequest(
                symbol=Symbol(request.ticker),
                timeframe=request.timeframe,
                start=request.start,
                end=request.end,
                limit=request.lookback,
                adjustment="raw",
                session="regular",
            )
            result = self.provider.get_bars(market_request)
        except Exception:  # noqa: BLE001  (benchmark fallback)
            result = None

        if result is None or not result.bars:
            last_close = 100.0
        else:
            last_close = result.bars[-1].close

        bars: list[ForecastBar] = []
        current = request.end + step
        for _ in range(request.horizon):
            bars.append(
                ForecastBar(
                    timestamp=current,
                    open=last_close,
                    high=last_close,
                    low=last_close,
                    close=last_close,
                    volume=0.0,
                )
            )
            current += step

        forecast = KronosForecast(
            ticker=str(request.ticker) if hasattr(request.ticker, "lower") else request.ticker,
            model_id=request.model_id,
            timeframe=request.timeframe,
            bars=bars,
            requested_start=request.start,
            requested_end=request.end,
            actual_start=bars[0].timestamp,
            actual_end=bars[-1].timestamp,
            lookback_bars=len(result.bars) if result else 0,
            horizon=request.horizon,
            generated_at=datetime.now(UTC),
            suitable_for_live_trading=False,
            warning="Last-value benchmark; not suitable for live trading",
        )
        self.cache.set(key, forecast)
        return forecast


class DriftForecastProvider(BenchmarkForecastProvider):
    """Extrapolates the mean historical return over the lookback window.

    Computes the average daily (or per-timeframe) return from the historical
    context and compounds it forward for the forecast horizon.  The
    direction of the forecast is the sign of the mean return.
    """

    MODEL_VERSION: ClassVar[str] = MODEL_DRIFT
    SUPPORTED_MODELS: ClassVar[list[str]] = [MODEL_DRIFT]
    CONTEXT_LENGTH: ClassVar[int] = 10000

    def __init__(
        self,
        provider: MarketDataProvider | None = None,
        cache: AbstractCache[KronosForecast] | None = None,
    ) -> None:
        super().__init__(cache)
        self.provider = provider if provider is not None else MockMarketDataProvider()

    def forecast(self, request: KronosForecastRequest) -> KronosForecast:
        key = self._cache_key(request, "drift")
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        step = _timeframe_step(request.timeframe)
        try:
            market_request = HistoricalBarsRequest(
                symbol=Symbol(request.ticker),
                timeframe=request.timeframe,
                start=request.start,
                end=request.end,
                limit=request.lookback,
                adjustment="raw",
                session="regular",
            )
            result = self.provider.get_bars(market_request)
        except Exception:  # noqa: BLE001  (benchmark fallback)
            result = None

        if result is not None and len(result.bars) >= 2:
            closes = [b.close for b in result.bars]
            returns = [(closes[i] - closes[i - 1]) / abs(closes[i - 1]) if closes[i - 1] != 0 else 0.0 for i in range(1, len(closes))]
            mean_return = sum(returns) / len(returns) if returns else 0.0
            last_close = closes[-1]
            lookback_count = len(closes)
        else:
            mean_return = 0.0
            last_close = 100.0
            lookback_count = 0

        bars: list[ForecastBar] = []
        current = request.end + step
        price = last_close
        for _ in range(request.horizon):
            open_price = price
            close_price = max(0.01, open_price * (1 + mean_return))
            high = max(open_price, close_price) + 0.5
            low = min(open_price, close_price) - 0.5
            bars.append(
                ForecastBar(
                    timestamp=current,
                    open=round(open_price, 4),
                    high=round(high, 4),
                    low=round(low, 4),
                    close=round(close_price, 4),
                    volume=0.0,
                )
            )
            price = close_price
            current += step

        forecast = KronosForecast(
            ticker=str(request.ticker) if hasattr(request.ticker, "lower") else request.ticker,
            model_id=request.model_id,
            timeframe=request.timeframe,
            bars=bars,
            requested_start=request.start,
            requested_end=request.end,
            actual_start=bars[0].timestamp,
            actual_end=bars[-1].timestamp,
            lookback_bars=lookback_count,
            horizon=request.horizon,
            generated_at=datetime.now(UTC),
            suitable_for_live_trading=False,
            warning="Drift benchmark; not suitable for live trading",
        )
        self.cache.set(key, forecast)
        return forecast


# ---------------------------------------------------------------------------
# Re-export SMAForecastProvider for convenience
# ---------------------------------------------------------------------------
from app.services.kronos import SMAForecastProvider  # noqa: F401

# ---------------------------------------------------------------------------
# Internal helpers (kept module-private)
# ---------------------------------------------------------------------------


def _deterministic_seed(request: KronosForecastRequest) -> int:
    """Generate a deterministic seed from the request parameters."""
    import hashlib

    material = (
        f"{request.ticker}|{request.model_id}|{request.timeframe}|"
        f"{request.start.isoformat()}|{request.end.isoformat()}|{request.lookback}|{request.horizon}"
    )
    return int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:8], "big")
