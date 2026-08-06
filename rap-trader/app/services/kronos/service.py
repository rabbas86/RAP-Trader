from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import Literal

from app.domain.models.market_data import HistoricalBarsRequest, MarketDataError, Symbol, Timeframe
from app.domain.models.prediction import KronosPrediction
from app.services.market_data import (
    AbstractCache,
    InMemoryCache,
    MarketDataProvider,
    MockMarketDataProvider,
    cache_key_builder,
)

DEFAULT_TIMEFRAME: Timeframe = "1d"
DEFAULT_LIMIT = 100
SHORT_WINDOW = 5
LONG_WINDOW = 20


class KronosService(ABC):
    @abstractmethod
    def predict(
        self,
        ticker: str,
        timeframe: Timeframe = DEFAULT_TIMEFRAME,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = DEFAULT_LIMIT,
    ) -> KronosPrediction:
        """Create a market prediction."""


class MockKronosService(KronosService):
    LIVE_TRADING_SUITABLE = False

    def predict(
        self,
        ticker: str,
        timeframe: Timeframe = DEFAULT_TIMEFRAME,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = DEFAULT_LIMIT,
    ) -> KronosPrediction:
        return KronosPrediction(
            ticker=ticker.upper(),
            direction="FLAT",
            confidence=0,
            expected_return=0,
            time_horizon="none (mock only; not suitable for live trading)",
            generated_at=datetime.now(UTC),
            model_version="mock-kronos-v0",
            timeframe=timeframe,
            source_provider="mock-kronos",
        )


class OfflineKronosService(KronosService):
    """Deterministic SMA forecast over normalized historical market data."""

    LIVE_TRADING_SUITABLE = False
    MODEL_VERSION = "offline-kronos-v0"

    def __init__(
        self,
        provider: MarketDataProvider | None = None,
        cache: AbstractCache[KronosPrediction] | None = None,
    ) -> None:
        self.provider = provider if provider is not None else MockMarketDataProvider()
        self.cache = cache if cache is not None else InMemoryCache(ttl_seconds=300.0)

    def _request(
        self,
        ticker: str,
        timeframe: Timeframe,
        start: datetime | None,
        end: datetime | None,
        limit: int | None,
    ) -> HistoricalBarsRequest:
        effective_end = end if end is not None else datetime(2026, 1, 1, tzinfo=UTC)
        effective_start = start if start is not None else effective_end - timedelta(days=365)
        return HistoricalBarsRequest(
            symbol=Symbol(ticker),
            timeframe=timeframe,
            start=effective_start,
            end=effective_end,
            limit=limit,
        )

    def _cache_key(self, request: HistoricalBarsRequest) -> str:
        provider_type = f"{type(self.provider).__module__}.{type(self.provider).__qualname__}"
        return cache_key_builder(
            "offline-kronos",
            request,
            request.adjustment,
            request.session,
            {
                "market_data_provider": provider_type,
                "model_version": self.MODEL_VERSION,
                "short_window": SHORT_WINDOW,
                "long_window": LONG_WINDOW,
            },
        )

    def _fallback(self, request: HistoricalBarsRequest, provider: str) -> KronosPrediction:
        return KronosPrediction(
            ticker=str(request.symbol),
            direction="FLAT",
            confidence=0.0,
            expected_return=0.0,
            time_horizon=request.timeframe,
            generated_at=request.end,
            model_version=self.MODEL_VERSION,
            timeframe=request.timeframe,
            source_provider=provider,
        )

    def predict(
        self,
        ticker: str,
        timeframe: Timeframe = DEFAULT_TIMEFRAME,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = DEFAULT_LIMIT,
    ) -> KronosPrediction:
        request = self._request(ticker, timeframe, start, end, limit)
        key = self._cache_key(request)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        try:
            result = self.provider.get_bars(request)
        except MarketDataError as exc:
            prediction = self._fallback(request, exc.provider)
            self.cache.set(key, prediction)
            return prediction

        closes = [bar.close for bar in result.bars]
        if len(closes) < LONG_WINDOW:
            prediction = self._fallback(request, result.provider)
        else:
            short_sma = sum(closes[-SHORT_WINDOW:]) / SHORT_WINDOW
            long_sma = sum(closes[-LONG_WINDOW:]) / LONG_WINDOW
            separation = short_sma - long_sma
            direction: Literal["UP", "DOWN", "FLAT"] = "UP" if separation > 0 else "DOWN" if separation < 0 else "FLAT"
            price = closes[-1]
            prediction = KronosPrediction(
                ticker=str(result.symbol),
                direction=direction,
                confidence=min(1.0, max(0.0, abs(separation) / price)),
                expected_return=separation / long_sma,
                time_horizon=result.timeframe,
                generated_at=result.retrieved_at,
                model_version=self.MODEL_VERSION,
                timeframe=result.timeframe,
                source_provider=result.provider,
                data_start=result.actual_start,
                data_end=result.actual_end,
            )
        self.cache.set(key, prediction)
        return prediction
