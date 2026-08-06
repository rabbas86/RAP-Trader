"""Phase 3: Offline Kronos forecasting providers.

This module implements a provider architecture for Kronos financial forecasting:
- KronosForecastProvider: abstract interface
- MockKronosProvider: deterministic, offline, produces valid future candles
- SMAForecastProvider: deterministic SMA crossover baseline (NOT the official Kronos model)
- LocalKronosProvider: adapter for the official Kronos model (lazy imports, offline-only by default)

The SMA provider is explicitly identified as a baseline heuristic and must not be
presented as the official Kronos model.
"""

from __future__ import annotations

import hashlib
import random
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Literal

from app.domain.models.kronos import (
    SMA_BASELINE_MODEL_ID,
    ForecastBar,
    KronosError,
    KronosErrorCodes,
    KronosForecast,
    KronosForecastMetrics,
    KronosForecastRequest,
    KronosModelId,
    KronosModelMetadata,
    KronosProviderHealth,
)
from app.domain.models.market_data import HistoricalBarsRequest, HistoricalBarsResult, MarketDataError, Symbol, Timeframe
from app.services.market_data import AbstractCache, InMemoryCache, MarketDataProvider, MockMarketDataProvider, cache_key_builder

DEFAULT_TIMEFRAME: Timeframe = "1d"
DEFAULT_LOOKBACK = 60
DEFAULT_HORIZON = 5
SUPPORTED_MODELS: list[str] = [
    KronosModelId.MOCK.value,
    KronosModelId.MINI.value,
    KronosModelId.SMALL.value,
    KronosModelId.BASE.value,
]


def _timeframe_delta(timeframe: Timeframe) -> timedelta:
    mapping: dict[str, timedelta] = {
        "1m": timedelta(minutes=1),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "1d": timedelta(days=1),
        "1w": timedelta(weeks=1),
    }
    return mapping[timeframe]


def _generate_future_timestamps(last_timestamp: datetime, horizon: int, timeframe: Timeframe) -> Any:
    """Generate a pandas DatetimeIndex of future timestamps starting one step after last_timestamp."""
    import pandas as pd  # type: ignore[import-untyped]

    step = _timeframe_delta(timeframe)
    timestamps = [last_timestamp + step * (i + 1) for i in range(horizon)]
    return pd.DatetimeIndex(timestamps)


def _timestamps_to_index(bars: list[Any]) -> Any:
    import pandas as pd

    return pd.DatetimeIndex([bar.timestamp for bar in bars])


def _validate_predicted_df(pred_df: Any, y_timestamp: Any, ticker: str, timeframe: Timeframe) -> list[ForecastBar]:
    """Convert a Kronos predicted DataFrame into validated ForecastBar models."""
    import pandas as pd

    if not isinstance(pred_df, pd.DataFrame):
        raise KronosError(
            KronosErrorCodes.MALFORMED_FORECAST,
            "Kronos prediction did not return a DataFrame",
            "local-kronos",
        )
    expected_columns = {"open", "high", "low", "close", "volume"}
    missing = expected_columns.difference(set(pred_df.columns))
    if missing:
        raise KronosError(
            KronosErrorCodes.MALFORMED_FORECAST,
            f"Kronos prediction missing columns: {sorted(missing)}",
            "local-kronos",
        )
    if len(pred_df) != len(y_timestamp):
        raise KronosError(
            KronosErrorCodes.MALFORMED_FORECAST,
            f"Prediction length {len(pred_df)} does not match horizon {len(y_timestamp)}",
            "local-kronos",
        )
    bars: list[ForecastBar] = []
    for i, row in pred_df.iterrows():
        idx = int(i) if hasattr(i, "__int__") else i
        timestamp = y_timestamp[idx] if idx < len(y_timestamp) else y_timestamp[-1]
        if hasattr(timestamp, "to_pydatetime"):
            timestamp = timestamp.to_pydatetime()
        bars.append(
            ForecastBar(
                timestamp=timestamp,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
        )
    return bars


class KronosForecastProvider(ABC):
    """Abstract interface for Kronos forecast providers."""

    @abstractmethod
    def forecast(self, request: KronosForecastRequest) -> KronosForecast:
        """Generate a forecast for the requested symbol and historical context."""

    @abstractmethod
    def health(self) -> KronosProviderHealth:
        """Return a non-invasive provider health snapshot."""

    @abstractmethod
    def supported_models(self) -> list[str]:
        """Return stable public model identifiers."""

    @abstractmethod
    def model_metadata(self, model_id: str) -> KronosModelMetadata:
        """Return metadata for a specific model identifier."""


class MockKronosProvider(KronosForecastProvider):
    """Deterministic, fully offline provider that produces valid future candles.

    The mock provider generates synthetic future OHLCV bars using a deterministic
    seed derived from the request parameters. It is the default provider for
    tests and the API when no real Kronos model is available.
    """

    LIVE_TRADING_SUITABLE: ClassVar[bool] = False
    MODEL_VERSION: ClassVar[str] = KronosModelId.MOCK.value
    SUPPORTED_MODELS: ClassVar[list[str]] = [KronosModelId.MOCK.value]
    CONTEXT_LENGTH: ClassVar[int] = 512

    def __init__(self, cache: AbstractCache[KronosForecast] | None = None) -> None:
        self.cache = cache if cache is not None else InMemoryCache[KronosForecast](ttl_seconds=300.0)

    def _cache_key(self, request: KronosForecastRequest) -> str:
        return cache_key_builder(
            "mock-kronos",
            HistoricalBarsRequest(
                symbol=Symbol(request.ticker),
                timeframe=request.timeframe,
                start=request.start,
                end=request.end,
                limit=request.lookback,
                adjustment="raw",
                session="regular",
            ),
            "raw",
            "regular",
            {
                "model_id": request.model_id,
                "horizon": request.horizon,
                "version": 1,
            },
        )

    def _seed(self, request: KronosForecastRequest) -> int:
        material = (
            f"{request.ticker}|{request.model_id}|{request.timeframe}|"
            f"{request.start.isoformat()}|{request.end.isoformat()}|{request.lookback}|{request.horizon}"
        )
        return int.from_bytes(hashlib.sha256(material.encode()).digest()[:8], "big")

    def forecast(self, request: KronosForecastRequest) -> KronosForecast:
        key = self._cache_key(request)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        seed = self._seed(request)
        rng = random.Random(seed)
        base_price = 100.0 + (seed % 5000) / 100
        step = _timeframe_delta(request.timeframe)
        bars: list[ForecastBar] = []
        current = request.end + step
        price = base_price
        for _i in range(request.horizon):
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
            warning="Mock forecast for testing; not suitable for live trading",
        )
        self.cache.set(key, result)
        return result

    def health(self) -> KronosProviderHealth:
        return KronosProviderHealth(
            provider="mock-kronos",
            configured=True,
            reachable=True,
            checked_at=datetime.now(UTC),
            status="healthy",
            detail="Deterministic synthetic provider is available",
            model_id=self.MODEL_VERSION,
        )

    def supported_models(self) -> list[str]:
        return list(self.SUPPORTED_MODELS)

    def model_metadata(self, model_id: str) -> KronosModelMetadata:
        if model_id not in self.SUPPORTED_MODELS:
            raise KronosError(
                KronosErrorCodes.UNSUPPORTED_MODEL,
                f"Model {model_id} is not supported by the mock provider",
                "mock-kronos",
            )
        return KronosModelMetadata(
            model_id=model_id,
            display_name="Mock Kronos",
            context_length=self.CONTEXT_LENGTH,
            description="Deterministic synthetic provider for testing and offline use",
        )


_SHORT_WINDOW = 5
_LONG_WINDOW = 20


class SMAForecastProvider(KronosForecastProvider):
    """Deterministic SMA crossover baseline forecast provider.

    This provider applies a simple-moving-average crossover strategy (5-period short
    vs 20-period long) over historical bars to produce a baseline forecast.

    NOTE: This is a heuristic baseline, NOT the official Kronos foundation model.
    It is explicitly identified as 'sma-baseline-v1' and does not claim to be Kronos.
    """

    LIVE_TRADING_SUITABLE: ClassVar[bool] = False
    MODEL_VERSION: ClassVar[str] = SMA_BASELINE_MODEL_ID
    SUPPORTED_MODELS: ClassVar[list[str]] = [MODEL_VERSION]
    CONTEXT_LENGTH: ClassVar[int] = 10_000
    SHORT_WINDOW: ClassVar[int] = _SHORT_WINDOW
    LONG_WINDOW: ClassVar[int] = _LONG_WINDOW

    def __init__(
        self,
        provider: MarketDataProvider | None = None,
        cache: AbstractCache[KronosForecast] | None = None,
    ) -> None:
        self.provider = provider if provider is not None else MockMarketDataProvider()
        self.cache = cache if cache is not None else InMemoryCache[KronosForecast](ttl_seconds=300.0)

    def _market_request(self, request: KronosForecastRequest) -> HistoricalBarsRequest:
        return HistoricalBarsRequest(
            symbol=Symbol(request.ticker),
            timeframe=request.timeframe,
            start=request.start,
            end=request.end,
            limit=request.lookback,
            adjustment="raw",
            session="regular",
        )

    def _cache_key(self, request: KronosForecastRequest) -> str:
        provider_type = f"{type(self.provider).__module__}.{type(self.provider).__qualname__}"
        return cache_key_builder(
            "sma-baseline",
            self._market_request(request),
            "raw",
            "regular",
            {
                "market_data_provider": provider_type,
                "model_version": self.MODEL_VERSION,
                "short_window": self.SHORT_WINDOW,
                "long_window": self.LONG_WINDOW,
                "horizon": request.horizon,
            },
        )

    def _fallback(self, request: KronosForecastRequest) -> KronosForecast:
        """Produce a flat zero-confidence forecast when SMA cannot be computed."""
        step = _timeframe_delta(request.timeframe)
        bars: list[ForecastBar] = []
        current = request.end + step
        for _i in range(request.horizon):
            bars.append(
                ForecastBar(
                    timestamp=current,
                    open=100.0,
                    high=100.5,
                    low=99.5,
                    close=100.0,
                    volume=0.0,
                )
            )
            current += step
        return KronosForecast(
            ticker=request.ticker,
            model_id=request.model_id,
            timeframe=request.timeframe,
            bars=bars,
            requested_start=request.start,
            requested_end=request.end,
            actual_start=bars[0].timestamp,
            actual_end=bars[-1].timestamp,
            lookback_bars=0,
            horizon=request.horizon,
            generated_at=datetime.now(UTC),
            suitable_for_live_trading=False,
            warning="Insufficient data for SMA baseline; flat forecast",
        )

    def forecast(self, request: KronosForecastRequest) -> KronosForecast:
        key = self._cache_key(request)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        try:
            market_request = self._market_request(request)
            result = self.provider.get_bars(market_request)
        except MarketDataError:
            forecast = self._fallback(request)
            self.cache.set(key, forecast)
            return forecast

        closes = [bar.close for bar in result.bars]
        if len(closes) < self.LONG_WINDOW:
            forecast = self._fallback(request)
            self.cache.set(key, forecast)
            return forecast

        short_sma = sum(closes[-self.SHORT_WINDOW :]) / self.SHORT_WINDOW
        long_sma = sum(closes[-self.LONG_WINDOW :]) / self.LONG_WINDOW
        separation = short_sma - long_sma
        if separation > 0:
            trend_bias = 0.01
        elif separation < 0:
            trend_bias = -0.01
        else:
            trend_bias = 0.0

        momentum = separation / long_sma
        last_close = closes[-1]
        step = _timeframe_delta(request.timeframe)
        bars: list[ForecastBar] = []
        current = request.end + step
        price = last_close
        for i in range(request.horizon):
            open_price = price
            close_price = max(0.01, open_price * (1 + momentum + trend_bias * (i + 1)))
            high = max(open_price, close_price) + 0.5
            low = min(open_price, close_price) - 0.5
            bars.append(
                ForecastBar(
                    timestamp=current,
                    open=round(open_price, 4),
                    high=round(high, 4),
                    low=round(low, 4),
                    close=round(close_price, 4),
                    volume=float(result.bars[-1].volume),
                )
            )
            price = close_price
            current += step

        forecast = KronosForecast(
            ticker=str(result.symbol),
            model_id=request.model_id,
            timeframe=result.timeframe,
            bars=bars,
            requested_start=request.start,
            requested_end=request.end,
            actual_start=bars[0].timestamp,
            actual_end=bars[-1].timestamp,
            lookback_bars=len(closes),
            horizon=request.horizon,
            generated_at=datetime.now(UTC),
            suitable_for_live_trading=False,
            warning="SMA baseline heuristic; not the official Kronos model",
        )
        self.cache.set(key, forecast)
        return forecast

    def health(self) -> KronosProviderHealth:
        provider_health = self.provider.health()
        return KronosProviderHealth(
            provider="sma-baseline",
            configured=True,
            reachable=provider_health.reachable,
            checked_at=datetime.now(UTC),
            status="healthy",
            detail="SMA crossover baseline; not the official Kronos model",
            model_id=self.MODEL_VERSION,
        )

    def supported_models(self) -> list[str]:
        return list(self.SUPPORTED_MODELS)

    def model_metadata(self, model_id: str) -> KronosModelMetadata:
        if model_id not in self.SUPPORTED_MODELS:
            raise KronosError(
                KronosErrorCodes.UNSUPPORTED_MODEL,
                f"Model {model_id} is not supported by the SMA baseline provider",
                "sma-baseline",
            )
        return KronosModelMetadata(
            model_id=model_id,
            display_name="SMA Baseline Forecast",
            context_length=self.CONTEXT_LENGTH,
            description="Deterministic 5/20-period SMA crossover heuristic; NOT the official Kronos model",
        )


class LocalKronosProvider(KronosForecastProvider):
    """Adapter for the official Kronos foundation model.

    Kronos is loaded lazily on first use. Torch, pandas, and the Kronos model
    package are imported only when this provider is explicitly selected and a
    valid local path or enabled remote identifier is provided. No model is
    downloaded at import or startup, and no inference runs outside an explicit
    forecast call.
    """

    LIVE_TRADING_SUITABLE: ClassVar[bool] = False
    SUPPORTED_MODELS: ClassVar[list[str]] = [
        KronosModelId.MINI.value,
        KronosModelId.SMALL.value,
        KronosModelId.BASE.value,
    ]

    _MODEL_INFO: ClassVar[dict[str, dict[str, Any]]] = {
        KronosModelId.MINI.value: {
            "display_name": "Kronos Mini",
            "context_length": 512,
            "description": "Lightweight Kronos model for K-line forecasting",
        },
        KronosModelId.SMALL.value: {
            "display_name": "Kronos Small",
            "context_length": 512,
            "description": "Small Kronos model for K-line forecasting",
        },
        KronosModelId.BASE.value: {
            "display_name": "Kronos Base",
            "context_length": 512,
            "description": "Base Kronos model for K-line forecasting",
        },
    }

    def __init__(
        self,
        market_data_provider: MarketDataProvider | None = None,
        model_path: str | None = None,
        tokenizer_path: str | None = None,
        model_id: str = KronosModelId.SMALL.value,
        device: str = "cpu",
        offline_only: bool = True,
    ) -> None:
        if model_id == KronosModelId.MOCK.value:
            raise KronosError(
                KronosErrorCodes.UNSUPPORTED_MODEL,
                "LocalKronosProvider cannot serve the mock model",
                "local-kronos",
            )
        if model_id == "kronos-large":
            raise KronosError(
                KronosErrorCodes.UNSUPPORTED_MODEL,
                "kronos-large is not supported",
                "local-kronos",
            )
        if model_id not in self.SUPPORTED_MODELS:
            raise KronosError(
                KronosErrorCodes.UNSUPPORTED_MODEL_ID,
                f"Unsupported Kronos model id: {model_id}",
                "local-kronos",
            )
        self.market_data_provider = market_data_provider if market_data_provider is not None else MockMarketDataProvider()
        self.model_path = model_path
        self.tokenizer_path = tokenizer_path
        self.model_id = model_id
        self.device = device
        self._offline_only = offline_only
        self._predictor: Any = None
        self._max_context = self._MODEL_INFO[model_id]["context_length"]

    def _load(self) -> None:
        """Lazily load the Kronos model, tokenizer, and predictor."""
        if self._predictor is not None:
            return
        try:
            from model import Kronos, KronosPredictor, KronosTokenizer  # type: ignore[import-not-found]
        except ImportError as exc:
            raise KronosError(
                KronosErrorCodes.MODEL_LOAD_FAILED,
                "Kronos model package is not installed",
                "local-kronos",
                internal_detail=repr(exc),
            ) from exc

        try:
            if self.model_path is not None:
                model: Any = Kronos.from_pretrained(self.model_path)
            else:
                if self._offline_only:
                    raise KronosError(
                        KronosErrorCodes.MODEL_LOAD_FAILED,
                        "Remote model loading is disabled (offline mode)",
                        "local-kronos",
                    )
                model = Kronos.from_pretrained(f"NeoQuasar/{self.model_id}")
        except KronosError:
            raise
        except Exception as exc:
            raise KronosError(
                KronosErrorCodes.MODEL_LOAD_FAILED,
                "Failed to load the Kronos model",
                "local-kronos",
                internal_detail=repr(exc),
            ) from exc

        try:
            if self.tokenizer_path is not None:
                tokenizer: Any = KronosTokenizer.from_pretrained(self.tokenizer_path)
            else:
                tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
        except Exception as exc:
            raise KronosError(
                KronosErrorCodes.MODEL_LOAD_FAILED,
                "Failed to load the Kronos tokenizer",
                "local-kronos",
                internal_detail=repr(exc),
            ) from exc

        self._predictor = KronosPredictor(
            model,
            tokenizer,
            device=self.device,
            max_context=self._max_context,
        )

    def forecast(self, request: KronosForecastRequest) -> KronosForecast:
        self._load()

        if self._predictor is None:
            raise KronosError(
                KronosErrorCodes.MODEL_LOAD_FAILED,
                "Kronos model was not loaded",
                "local-kronos",
            )

        market_request = HistoricalBarsRequest(
            symbol=Symbol(request.ticker),
            timeframe=request.timeframe,
            start=request.start,
            end=request.end,
            limit=request.lookback,
            adjustment="raw",
            session="regular",
        )
        try:
            result: HistoricalBarsResult = self.market_data_provider.get_bars(market_request)
        except MarketDataError as exc:
            raise KronosError(
                KronosErrorCodes.PROVIDER_UNAVAILABLE,
                "Market data provider failed",
                "local-kronos",
                retryable=exc.retryable,
                internal_detail=exc.internal_detail,
            ) from exc

        if len(result.bars) < self._max_context:
            raise KronosError(
                KronosErrorCodes.INSUFFICIENT_HISTORY,
                f"Insufficient history: {len(result.bars)} bars, need at least {self._max_context}",
                "local-kronos",
            )

        if len(result.bars) > self._max_context:
            trimmed = result.bars[-self._max_context :]
            result = HistoricalBarsResult(
                symbol=result.symbol,
                timeframe=result.timeframe,
                bars=trimmed,
                provider=result.provider,
                requested_start=result.requested_start,
                requested_end=result.actual_end,
                actual_start=trimmed[0].timestamp,
                actual_end=result.actual_end,
                adjustment=result.adjustment,
                session=result.session,
                currency=result.currency,
                exchange=result.exchange,
                partial=True,
                retrieved_at=result.retrieved_at,
            )

        x_timestamp = _timestamps_to_index(result.bars)
        y_timestamp = _generate_future_timestamps(result.actual_end, request.horizon, request.timeframe)

        import pandas as pd

        x_df = pd.DataFrame(
            {
                "open": [b.open for b in result.bars],
                "high": [b.high for b in result.bars],
                "low": [b.low for b in result.bars],
                "close": [b.close for b in result.bars],
                "volume": [b.volume for b in result.bars],
            }
        )

        try:
            pred_df = self._predictor.predict(x_df, x_timestamp, y_timestamp, pred_len=request.horizon)
        except Exception as exc:
            raise KronosError(
                KronosErrorCodes.INFERENCE_FAILED,
                "Kronos inference failed",
                "local-kronos",
                internal_detail=repr(exc),
            ) from exc

        bars = _validate_predicted_df(pred_df, y_timestamp, request.ticker, request.timeframe)

        forecast = KronosForecast(
            ticker=str(result.symbol),
            model_id=self.model_id,
            timeframe=result.timeframe,
            bars=bars,
            requested_start=request.start,
            requested_end=request.end,
            actual_start=bars[0].timestamp,
            actual_end=bars[-1].timestamp,
            lookback_bars=len(result.bars),
            horizon=request.horizon,
            generated_at=datetime.now(UTC),
            suitable_for_live_trading=False,
            warning=None,
        )
        return forecast

    def health(self) -> KronosProviderHealth:
        loaded = self._predictor is not None
        return KronosProviderHealth(
            provider="local-kronos",
            configured=True,
            reachable=None,
            checked_at=datetime.now(UTC),
            status="degraded" if not loaded else "healthy",
            detail=("Model configured but not loaded" if not loaded else f"Kronos model {self.model_id} loaded on {self.device}"),
            model_id=self.model_id,
        )

    def supported_models(self) -> list[str]:
        return list(self.SUPPORTED_MODELS)

    def model_metadata(self, model_id: str) -> KronosModelMetadata:
        if model_id not in self.SUPPORTED_MODELS:
            raise KronosError(
                KronosErrorCodes.UNSUPPORTED_MODEL,
                f"Model {model_id} is not supported by LocalKronosProvider",
                "local-kronos",
            )
        info = self._MODEL_INFO[model_id]
        return KronosModelMetadata(
            model_id=model_id,
            display_name=info["display_name"],
            context_length=info["context_length"],
            description=info["description"],
        )


class KronosInputAdapter:
    """Adapts HistoricalBarsResult for the Kronos prediction pipeline.

    Responsibilities:
    - Enforces lookback (max sample count)
    - Preserves UTC, adjustment, and session semantics
    - Validates regular spacing of historical timestamps
    - Constructs future timestamps for the forecast horizon
    - Rejects partial data unless explicitly allowed
    - Does NOT resample or silently fill missing bars
    """

    def __init__(
        self,
        max_lookback: int = DEFAULT_LOOKBACK,
        max_sample_count: int = 10000,
        allow_partial: bool = False,
    ) -> None:
        self.max_lookback = max_lookback
        self.max_sample_count = max_sample_count
        self.allow_partial = allow_partial

    def prepare(
        self,
        result: HistoricalBarsResult,
        lookback: int,
        horizon: int,
        timeframe: Timeframe,
    ) -> tuple[list[Any], list[Any], list[ForecastBar]]:
        """Prepare historical bars and future timestamps for Kronos prediction.

        Returns:
            A tuple of (historical_bars, future_timestamps, future_bars_placeholder)
        """
        bars = result.bars

        if len(bars) > self.max_sample_count:
            raise KronosError(
                KronosErrorCodes.INPUT_TOO_LONG,
                f"Input has {len(bars)} bars, exceeds max_sample_count of {self.max_sample_count}",
                "local-kronos",
            )

        effective_lookback = min(lookback, len(bars))
        if effective_lookback < lookback:
            if not self.allow_partial:
                raise KronosError(
                    KronosErrorCodes.INSUFFICIENT_HISTORY,
                    f"Requested lookback {lookback} but only {len(bars)} bars available; "
                    "partial data rejected (set allow_partial=True to permit)",
                    "local-kronos",
                )
            effective_lookback = len(bars)

        if lookback > self.max_lookback:
            raise KronosError(
                KronosErrorCodes.INPUT_TOO_LONG,
                f"Requested lookback {lookback} exceeds max_lookback of {self.max_lookback}",
                "local-kronos",
            )

        if effective_lookback > self.max_lookback:
            bars = bars[-self.max_lookback :]
            effective_lookback = self.max_lookback

        if effective_lookback < len(bars):
            bars = bars[-effective_lookback:]

        self._validate_spacing(bars, timeframe)

        historical_bars = list(bars)
        last_timestamp = bars[-1].timestamp
        future_timestamps = _generate_future_timestamps(last_timestamp, horizon, timeframe)
        future_bars: list[ForecastBar] = []

        return historical_bars, future_timestamps, future_bars

    def _validate_spacing(self, bars: list[Any], timeframe: Timeframe) -> None:
        """Validate that historical timestamps are regularly spaced."""
        step = _timeframe_delta(timeframe)
        for i in range(1, len(bars)):
            delta = bars[i].timestamp - bars[i - 1].timestamp
            if abs(delta - step) > timedelta(seconds=1):
                raise KronosError(
                    KronosErrorCodes.IRREGULAR_SPACING,
                    f"Irregular spacing at index {i}: expected {step}, got {delta}",
                    "local-kronos",
                )


def _compute_expected_return(bars: list[ForecastBar]) -> float:
    if not bars:
        return 0.0
    if bars[0].open == 0:
        return 0.0
    return (bars[-1].close - bars[0].open) / abs(bars[0].open)


def _compute_volatility(bars: list[ForecastBar]) -> float:
    if len(bars) < 2:
        return 0.0
    returns: list[float] = []
    for i in range(1, len(bars)):
        if bars[i - 1].close == 0:
            continue
        ret = (bars[i].close - bars[i - 1].close) / abs(bars[i - 1].close)
        returns.append(float(ret))
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return float(variance**0.5)


def _compute_max_drawdown(bars: list[ForecastBar]) -> float:
    if not bars:
        return 0.0
    peaks: list[float] = []
    current_peak = bars[0].high
    for bar in bars:
        current_peak = max(current_peak, bar.high)
        drawdown = (current_peak - bar.close) / (current_peak if current_peak != 0 else 1.0)
        peaks.append(max(0.0, drawdown))
    return max(peaks) if peaks else 0.0


class KronosForecastMetricsService:
    """Deterministic service that derives UP/DOWN/FLAT metrics from a completed KronosForecast.

    Direction is descriptive metadata computed from the forecast bars, NOT a trading
    decision. The forecast itself contains future OHLCV candles.
    """

    def __init__(self, model_version: str = SMA_BASELINE_MODEL_ID, flat_threshold: float = 0.005) -> None:
        self.model_version = model_version
        self.flat_threshold = flat_threshold

    def compute(self, forecast: KronosForecast) -> KronosForecastMetrics:
        bars = forecast.bars
        if not bars:
            raise KronosError(
                KronosErrorCodes.MALFORMED_FORECAST,
                "Cannot compute metrics from a forecast with no bars",
                "metrics",
            )

        expected_return = _compute_expected_return(bars)
        volatility = _compute_volatility(bars)
        max_drawdown = _compute_max_drawdown(bars)
        upward_count = sum(1 for i in range(1, len(bars)) if bars[i].close > bars[i - 1].close)
        upward_bar_ratio = upward_count / (len(bars) - 1) if len(bars) > 1 else 0.0
        first_close = bars[0].close
        final_close = bars[-1].close
        max_high = max(b.high for b in bars)
        min_low = min(b.low for b in bars)

        if expected_return > self.flat_threshold:
            direction: Literal["UP", "DOWN", "FLAT"] = "UP"
        elif expected_return < -self.flat_threshold:
            direction = "DOWN"
        else:
            direction = "FLAT"

        confidence = min(1.0, abs(expected_return) / (self.flat_threshold * 2)) if self.flat_threshold > 0 else 0.0

        return KronosForecastMetrics(
            expected_return=round(expected_return, 6),
            volatility=round(volatility, 6),
            max_drawdown=round(max_drawdown, 6),
            upward_bar_ratio=round(upward_bar_ratio, 4),
            first_close=first_close,
            final_close=final_close,
            max_high=max_high,
            min_low=min_low,
            direction=direction,
            direction_confidence=round(confidence, 4),
            model_version=self.model_version,
        )
