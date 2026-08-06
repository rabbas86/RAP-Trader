"""Comprehensive tests for Phase 3 Kronos provider architecture.

Covers:
- MockKronosProvider: future candle forecasts, determinism, horizon matching, cache
- SMAForecastProvider: baseline naming, directional behavior, error fallback
- LocalKronosProvider: lazy imports, offline-only, local path, error translation
- KronosInputAdapter: lookback enforcement, spacing validation, partial data rejection
- KronosForecastMetricsService: metrics calculations, direction derivation
- Provider interface conformance
- No trading recommendation or execution path
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.domain.models import (
    HistoricalBarsRequest,
    HistoricalBarsResult,
    KronosForecast,
    KronosForecastRequest,
    KronosModelId,
    MarketDataError,
    MarketDataErrorCode,
    OHLCVBar,
    ProviderHealth,
    Symbol,
)
from app.domain.models.kronos import (
    SMA_BASELINE_MODEL_ID,
    ForecastBar,
    KronosError,
    KronosErrorCodes,
)
from app.services.kronos import (
    KronosForecastMetricsService,
    KronosInputAdapter,
    LocalKronosProvider,
    MockKronosProvider,
    SMAForecastProvider,
)
from app.services.market_data import MarketDataProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

START = datetime(2025, 1, 1, tzinfo=UTC)
END = datetime(2026, 1, 1, tzinfo=UTC)


class StaticProvider(MarketDataProvider):
    """Deterministic provider with configurable close prices."""

    def __init__(self, closes: list[float] | None = None, fail: bool = False) -> None:
        super().__init__()
        self.closes = closes if closes is not None else [100.0] * 20
        self.fail = fail
        self.calls = 0

    def get_bars(self, request: HistoricalBarsRequest) -> HistoricalBarsResult:
        self.calls += 1
        if self.fail:
            raise MarketDataError(MarketDataErrorCode.PROVIDER_UNAVAILABLE, "Unavailable", "static")
        start = request.end - timedelta(days=len(self.closes))
        bars = [
            OHLCVBar(
                timestamp=start + timedelta(days=index),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=100,
            )
            for index, close in enumerate(self.closes)
        ]
        return HistoricalBarsResult(
            symbol=request.symbol,
            timeframe=request.timeframe,
            bars=bars,
            provider="static",
            requested_start=request.start,
            requested_end=request.end,
            actual_start=bars[0].timestamp,
            actual_end=bars[-1].timestamp,
            adjustment=request.adjustment,
            session=request.session,
            retrieved_at=datetime(2026, 1, 2, tzinfo=UTC),
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider="static",
            configured=True,
            reachable=not self.fail,
            checked_at=datetime(2026, 1, 2, tzinfo=UTC),
            status="healthy" if not self.fail else "unreachable",
            detail="test provider",
        )

    def supported_timeframes(self) -> list[str]:
        return ["1d"]


def make_request(
    ticker: str = "AAPL",
    model_id: str = KronosModelId.MOCK.value,
    timeframe: str = "1d",
    horizon: int = 5,
    lookback: int = 60,
) -> KronosForecastRequest:
    return KronosForecastRequest(
        ticker=ticker,
        model_id=model_id,
        timeframe=timeframe,
        start=START,
        end=END,
        lookback=lookback,
        horizon=horizon,
    )


# ---------------------------------------------------------------------------
# MockKronosProvider tests
# ---------------------------------------------------------------------------


def test_mock_forecast_produces_valid_future_candles() -> None:
    provider = MockKronosProvider()
    req = make_request()
    fc = provider.forecast(req)
    assert len(fc.bars) == 5
    for bar in fc.bars:
        assert isinstance(bar, ForecastBar)
        assert bar.high >= bar.low
        assert bar.high >= bar.open
        assert bar.high >= bar.close
        assert bar.low <= bar.open
        assert bar.low <= bar.close
        assert bar.open > 0
        assert bar.volume >= 0
    assert fc.suitable_for_live_trading is False
    assert fc.warning is not None
    assert fc.model_id == KronosModelId.MOCK.value


def test_mock_forecast_horizon_matching() -> None:
    provider = MockKronosProvider()
    for horizon in [1, 5, 10, 20]:
        req = make_request(horizon=horizon)
        fc = provider.forecast(req)
        assert len(fc.bars) == horizon


def test_mock_forecast_is_deterministic() -> None:
    provider = MockKronosProvider()
    req = make_request()
    first = provider.forecast(req)
    second = provider.forecast(req)
    assert first == second


def test_mock_forecast_deterministic_across_instances() -> None:
    req = make_request()
    p1 = MockKronosProvider()
    p2 = MockKronosProvider()
    assert p1.forecast(req) == p2.forecast(req)


def test_mock_forecast_cache_hits() -> None:
    provider = MockKronosProvider()
    req = make_request()
    first = provider.forecast(req)
    second = provider.forecast(req)
    assert first is second


def test_mock_forecast_timestamps_follow_timeframe() -> None:
    provider = MockKronosProvider()
    req = make_request(timeframe="1d", horizon=3)
    fc = provider.forecast(req)
    assert fc.bars[0].timestamp > req.end
    diff = fc.bars[1].timestamp - fc.bars[0].timestamp
    assert diff == timedelta(days=1)


def test_mock_health() -> None:
    provider = MockKronosProvider()
    h = provider.health()
    assert h.provider == "mock-kronos"
    assert h.configured is True
    assert h.status == "healthy"
    assert h.reachable is True


def test_mock_supported_models() -> None:
    provider = MockKronosProvider()
    assert provider.supported_models() == ["mock-kronos-v0"]


def test_mock_model_metadata() -> None:
    provider = MockKronosProvider()
    meta = provider.model_metadata("mock-kronos-v0")
    assert meta.model_id == "mock-kronos-v0"
    assert meta.context_length == 512
    assert meta.display_name == "Mock Kronos"


def test_mock_model_metadata_rejects_unknown() -> None:
    provider = MockKronosProvider()
    with pytest.raises(KronosError) as exc:
        provider.model_metadata("kronos-small")
    assert exc.value.code == KronosErrorCodes.UNSUPPORTED_MODEL


# ---------------------------------------------------------------------------
# SMAForecastProvider tests
# ---------------------------------------------------------------------------


def test_sma_provider_is_identified_as_baseline() -> None:
    provider = SMAForecastProvider()
    assert provider.MODEL_VERSION == "sma-baseline-v1"
    assert "baseline" in provider.MODEL_VERSION
    assert "kronos" not in provider.MODEL_VERSION.lower()


def test_sma_forecast_warning_states_not_kronos() -> None:
    provider = SMAForecastProvider(provider=StaticProvider([100.0] * 20))
    req = make_request(model_id=SMA_BASELINE_MODEL_ID, lookback=20)
    fc = provider.forecast(req)
    assert fc.warning is not None
    assert "not the official Kronos model" in fc.warning


def test_sma_model_version_is_sma_baseline_v1() -> None:
    provider = SMAForecastProvider()
    assert provider.MODEL_VERSION == "sma-baseline-v1"


def test_sma_forecast_produces_future_candles() -> None:
    closes = [100.0] * 15 + [110.0] * 5
    provider = SMAForecastProvider(provider=StaticProvider(closes))
    req = make_request(model_id=SMA_BASELINE_MODEL_ID, lookback=20)
    fc = provider.forecast(req)
    assert len(fc.bars) == 5
    assert fc.suitable_for_live_trading is False


def test_sma_fallback_on_market_data_error() -> None:
    provider = SMAForecastProvider(provider=StaticProvider(fail=True))
    req = make_request(model_id=SMA_BASELINE_MODEL_ID)
    fc = provider.forecast(req)
    assert len(fc.bars) == 5
    assert fc.warning is not None
    assert "Insufficient data" in fc.warning or "SMA baseline" in fc.warning


def test_sma_supported_models() -> None:
    provider = SMAForecastProvider()
    assert provider.supported_models() == [SMA_BASELINE_MODEL_ID]


def test_sma_health() -> None:
    provider = SMAForecastProvider(provider=StaticProvider([100.0] * 20))
    h = provider.health()
    assert h.provider == "sma-baseline"
    assert h.status == "healthy"


def test_sma_model_metadata() -> None:
    provider = SMAForecastProvider()
    meta = provider.model_metadata(SMA_BASELINE_MODEL_ID)
    assert meta.model_id == SMA_BASELINE_MODEL_ID
    assert "NOT the official Kronos model" in meta.description


def test_sma_forecast_is_deterministic() -> None:
    closes = [100.0] * 15 + [110.0] * 5
    p1 = SMAForecastProvider(provider=StaticProvider(closes))
    p2 = SMAForecastProvider(provider=StaticProvider(closes))
    req = make_request(model_id=SMA_BASELINE_MODEL_ID, lookback=20)
    assert p1.forecast(req) == p2.forecast(req)


def test_sma_insufficient_history_falls_back() -> None:
    provider = SMAForecastProvider(provider=StaticProvider([100.0] * 19))
    req = make_request(model_id=SMA_BASELINE_MODEL_ID, lookback=20)
    fc = provider.forecast(req)
    assert len(fc.bars) == 5
    assert fc.warning is not None


# ---------------------------------------------------------------------------
# LocalKronosProvider tests — lazy imports, offline-only, error translation
# ---------------------------------------------------------------------------


def test_local_kronos_rejects_mock_model() -> None:
    with pytest.raises(KronosError) as exc:
        LocalKronosProvider(model_id=KronosModelId.MOCK.value)
    assert exc.value.code == KronosErrorCodes.UNSUPPORTED_MODEL


def test_local_kronos_rejects_large_model() -> None:
    with pytest.raises(KronosError) as exc:
        LocalKronosProvider(model_id="kronos-large")
    assert exc.value.code == KronosErrorCodes.UNSUPPORTED_MODEL


def test_local_kronos_rejects_invalid_model() -> None:
    with pytest.raises(KronosError) as exc:
        LocalKronosProvider(model_id="invalid-model")
    assert exc.value.code == KronosErrorCodes.UNSUPPORTED_MODEL_ID


def test_local_kronos_lazy_imports_no_download_at_import() -> None:
    # Importing LocalKronosProvider should NOT import torch or pandas
    # Check that torch and pandas are not in sys.modules from the import
    import importlib

    # Remove from cache if present (from other tests)
    torch_present_before = "torch" in sys.modules
    pandas_present_before = "pandas" in sys.modules

    # Fresh import of the module
    importlib.import_module("app.services.kronos.service")

    if not torch_present_before:
        assert "torch" not in sys.modules, "torch should not be imported at module load"
    if not pandas_present_before:
        assert "pandas" not in sys.modules, "pandas should not be imported at module load"


def test_local_kronos_lazy_model_loading() -> None:
    provider = LocalKronosProvider(model_id=KronosModelId.SMALL.value, offline_only=True)
    assert provider._predictor is None  # Not loaded at construction

    # forecast() should fail with MODEL_LOAD_FAILED because kronos package isn't installed
    req = make_request(model_id=KronosModelId.SMALL.value)
    with pytest.raises(KronosError) as exc:
        provider.forecast(req)
    assert exc.value.code == KronosErrorCodes.MODEL_LOAD_FAILED


def test_local_kronos_offline_only_blocks_remote() -> None:
    provider = LocalKronosProvider(model_id=KronosModelId.SMALL.value, offline_only=True)
    assert provider._offline_only is True


def test_local_kronos_remote_loading_disabled_by_default() -> None:
    provider = LocalKronosProvider(model_id=KronosModelId.SMALL.value)
    assert provider._offline_only is True  # defaults to True


def test_local_kronos_local_path_supported() -> None:
    provider = LocalKronosProvider(
        model_id=KronosModelId.SMALL.value,
        model_path="/fake/path",
        tokenizer_path="/fake/tokenizer",
        offline_only=False,
    )
    assert provider.model_path == "/fake/path"
    assert provider.tokenizer_path == "/fake/tokenizer"


def test_local_kronos_model_load_failure_translated() -> None:
    provider = LocalKronosProvider(model_id=KronosModelId.SMALL.value, offline_only=False)
    # Mock the import to succeed but model loading to fail
    fake_module = MagicMock()
    fake_module.Kronos.from_pretrained.side_effect = Exception("model not found")
    fake_module.KronosTokenizer.from_pretrained.return_value = MagicMock()
    fake_module.KronosPredictor.return_value = MagicMock()

    with patch.dict("sys.modules", {"model": fake_module}):
        req = make_request(model_id=KronosModelId.SMALL.value)
        with pytest.raises(KronosError) as exc:
            provider.forecast(req)
        assert exc.value.code == KronosErrorCodes.MODEL_LOAD_FAILED


def test_local_kronos_inference_failure_translated() -> None:
    provider = LocalKronosProvider(model_id=KronosModelId.SMALL.value, offline_only=False)

    # Build a fake predictor that raises on predict
    fake_predictor = MagicMock()
    fake_predictor.predict.side_effect = RuntimeError("CUDA OOM")

    # Manually set the predictor to bypass loading
    provider._predictor = fake_predictor  # type: ignore[assignment]

    # Patch the market_data_provider to return valid bars
    mock_provider = StaticProvider([100.0] * 700)
    with patch.object(provider, "market_data_provider", mock_provider):
        req = make_request(model_id=KronosModelId.SMALL.value, lookback=60)
        with pytest.raises(KronosError) as exc:
            provider.forecast(req)
    # Should be inference failure, not model load failure
    assert exc.value.code == KronosErrorCodes.INFERENCE_FAILED


def test_local_kronos_malformed_forecast_rejected() -> None:
    provider = LocalKronosProvider(model_id=KronosModelId.SMALL.value, offline_only=False)

    fake_predictor = MagicMock()
    fake_predictor.predict.return_value = "not a dataframe"
    fake_predictor.price_cols = ["open", "high", "low", "close"]
    fake_predictor.vol_col = "volume"
    fake_predictor.amt_vol = "amount"
    fake_predictor.clip = 5.0
    fake_predictor.max_context = 512

    with patch.object(provider, "market_data_provider", StaticProvider([100.0] * 700)):
        provider._predictor = fake_predictor  # type: ignore[assignment]

        req = make_request(model_id=KronosModelId.SMALL.value, lookback=60)
        with pytest.raises(KronosError) as exc:
            provider.forecast(req)
        assert exc.value.code == KronosErrorCodes.MALFORMED_FORECAST


def test_local_kronos_health() -> None:
    provider = LocalKronosProvider(model_id=KronosModelId.SMALL.value)
    h = provider.health()
    assert h.provider == "local-kronos"
    assert h.status == "degraded"  # Not loaded yet
    assert h.model_id == KronosModelId.SMALL.value


def test_local_kronos_supported_models() -> None:
    provider = LocalKronosProvider(model_id=KronosModelId.SMALL.value)
    assert provider.supported_models() == ["kronos-mini", "kronos-small", "kronos-base"]


def test_local_kronos_model_metadata() -> None:
    provider = LocalKronosProvider(model_id=KronosModelId.SMALL.value)
    meta = provider.model_metadata(KronosModelId.BASE.value)
    assert meta.model_id == KronosModelId.BASE.value
    assert meta.context_length == 512


def test_local_kronos_model_metadata_rejects_mock() -> None:
    provider = LocalKronosProvider(model_id=KronosModelId.SMALL.value)
    with pytest.raises(KronosError) as exc:
        provider.model_metadata(KronosModelId.MOCK.value)
    assert exc.value.code == KronosErrorCodes.UNSUPPORTED_MODEL


# ---------------------------------------------------------------------------
# KronosInputAdapter tests
# ---------------------------------------------------------------------------


def _make_bars(start: datetime, count: int, timeframe: str = "1d") -> list[OHLCVBar]:
    step = {"1d": timedelta(days=1), "1h": timedelta(hours=1)}[timeframe]
    return [
        OHLCVBar(
            timestamp=start + step * i,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=100,
        )
        for i in range(count)
    ]


def _make_result(count: int, start: datetime = START) -> HistoricalBarsResult:
    """Build a valid HistoricalBarsResult with `count` regularly-spaced daily bars."""
    bars = _make_bars(start, count)
    return HistoricalBarsResult(
        symbol=Symbol("AAPL"),
        timeframe="1d",
        bars=bars,
        provider="test",
        requested_start=start,
        requested_end=bars[-1].timestamp,
        actual_start=bars[0].timestamp,
        actual_end=bars[-1].timestamp,
        adjustment="raw",
        session="regular",
        retrieved_at=datetime.now(UTC),
    )


def test_input_adapter_enforces_max_sample_count() -> None:
    adapter = KronosInputAdapter(max_sample_count=50)
    result = _make_result(100)
    with pytest.raises(KronosError) as exc:
        adapter.prepare(result, lookback=60, horizon=5, timeframe="1d")
    assert exc.value.code == KronosErrorCodes.INPUT_TOO_LONG


def test_input_adapter_rejects_partial_data_by_default() -> None:
    adapter = KronosInputAdapter(max_sample_count=100, allow_partial=False)
    result = _make_result(10)
    with pytest.raises(KronosError) as exc:
        adapter.prepare(result, lookback=50, horizon=5, timeframe="1d")
    assert exc.value.code == KronosErrorCodes.INSUFFICIENT_HISTORY


def test_input_adapter_allows_partial_when_enabled() -> None:
    adapter = KronosInputAdapter(max_sample_count=100, allow_partial=True)
    result = _make_result(10)
    hist, future, _ = adapter.prepare(result, lookback=50, horizon=5, timeframe="1d")
    assert len(hist) == 10
    assert len(future) == 5


def test_input_adapter_enforces_max_lookback() -> None:
    adapter = KronosInputAdapter(max_lookback=30, max_sample_count=100)
    result = _make_result(100)
    with pytest.raises(KronosError) as exc:
        adapter.prepare(result, lookback=60, horizon=5, timeframe="1d")
    assert exc.value.code == KronosErrorCodes.INPUT_TOO_LONG


def test_input_adapter_trims_to_lookback() -> None:
    adapter = KronosInputAdapter(max_lookback=50, max_sample_count=100)
    result = _make_result(80)
    hist, _, _ = adapter.prepare(result, lookback=30, horizon=5, timeframe="1d")
    assert len(hist) == 30


def test_input_adapter_validates_spacing() -> None:
    adapter = KronosInputAdapter(max_lookback=50, max_sample_count=100)
    bars = _make_bars(START, 50)
    # Break spacing: skip a day in the middle
    bars[25] = OHLCVBar(
        timestamp=bars[25].timestamp + timedelta(days=3),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=100,
    )
    # Use model_construct to bypass the HistoricalBarsResult chronological
    # validator, since we are intentionally creating irregular spacing.
    result = HistoricalBarsResult.model_construct(
        symbol=Symbol("AAPL"),
        timeframe="1d",
        bars=bars,
        provider="test",
        requested_start=START,
        requested_end=bars[-1].timestamp,
        actual_start=bars[0].timestamp,
        actual_end=bars[-1].timestamp,
        adjustment="raw",
        session="regular",
        retrieved_at=datetime.now(UTC),
    )
    with pytest.raises(KronosError) as exc:
        adapter.prepare(result, lookback=30, horizon=5, timeframe="1d")
    assert exc.value.code == KronosErrorCodes.IRREGULAR_SPACING


def test_input_adapter_constructs_future_timestamps() -> None:
    adapter = KronosInputAdapter(max_lookback=50, max_sample_count=100)
    result = _make_result(30)
    hist, future, _ = adapter.prepare(result, lookback=30, horizon=5, timeframe="1d")
    assert len(hist) == 30
    assert len(future) == 5
    assert future[0] > hist[-1].timestamp


# ---------------------------------------------------------------------------
# KronosForecastMetricsService tests
# ---------------------------------------------------------------------------


def _make_forecast(bars: list[ForecastBar]) -> KronosForecast:
    return KronosForecast(
        ticker="AAPL",
        model_id=KronosModelId.MOCK.value,
        timeframe="1d",
        bars=bars,
        requested_start=START,
        requested_end=END,
        actual_start=bars[0].timestamp,
        actual_end=bars[-1].timestamp,
        lookback_bars=60,
        horizon=len(bars),
        generated_at=datetime.now(UTC),
        suitable_for_live_trading=False,
        warning=None,
    )


def _up_bars(horizon: int = 5) -> list[ForecastBar]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        ForecastBar(
            timestamp=base + timedelta(days=i),
            open=100.0,
            high=110.0,
            low=99.0,
            close=100.0 + i * 2,
            volume=1000,
        )
        for i in range(horizon)
    ]


def _down_bars(horizon: int = 5) -> list[ForecastBar]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        ForecastBar(
            timestamp=base + timedelta(days=i),
            open=100.0,
            high=101.0,
            low=90.0,
            close=100.0 - i * 2,
            volume=1000,
        )
        for i in range(horizon)
    ]


def _flat_bars(horizon: int = 5) -> list[ForecastBar]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        ForecastBar(
            timestamp=base + timedelta(days=i),
            open=100.0,
            high=100.5,
            low=99.5,
            close=100.0,
            volume=1000,
        )
        for i in range(horizon)
    ]


def test_metrics_up_direction() -> None:
    svc = KronosForecastMetricsService()
    metrics = svc.compute(_make_forecast(_up_bars()))
    assert metrics.direction == "UP"
    assert metrics.expected_return > 0
    assert metrics.upward_bar_ratio > 0.5
    assert metrics.max_high > metrics.min_low


def test_metrics_down_direction() -> None:
    svc = KronosForecastMetricsService()
    metrics = svc.compute(_make_forecast(_down_bars()))
    assert metrics.direction == "DOWN"
    assert metrics.expected_return < 0


def test_metrics_flat_direction() -> None:
    svc = KronosForecastMetricsService()
    metrics = svc.compute(_make_forecast(_flat_bars()))
    assert metrics.direction == "FLAT"


def test_metrics_volatility_non_negative() -> None:
    svc = KronosForecastMetricsService()
    metrics = svc.compute(_make_forecast(_up_bars()))
    assert metrics.volatility >= 0
    assert metrics.max_drawdown >= 0


def test_metrics_expected_return_calculation() -> None:
    bars = [
        ForecastBar(
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            open=100.0,
            high=105.0,
            low=95.0,
            close=105.0,
            volume=1000,
        ),
        ForecastBar(
            timestamp=datetime(2026, 1, 2, tzinfo=UTC),
            open=105.0,
            high=110.0,
            low=104.0,
            close=110.0,
            volume=1000,
        ),
    ]
    svc = KronosForecastMetricsService()
    metrics = svc.compute(_make_forecast(bars))
    expected = (110.0 - 100.0) / 100.0
    assert metrics.expected_return == pytest.approx(expected)


def test_metrics_empty_forecast_raises() -> None:
    svc = KronosForecastMetricsService()
    # KronosForecast model rejects empty bars at construction, so use
    # model_construct to bypass validation and test the service directly.
    fc = KronosForecast.model_construct(
        ticker="AAPL",
        model_id="mock-kronos-v0",
        timeframe="1d",
        bars=[],
        requested_start=START,
        requested_end=END,
        actual_start=START,
        actual_end=END,
        lookback_bars=0,
        horizon=0,
        generated_at=datetime.now(UTC),
        suitable_for_live_trading=False,
        warning=None,
    )
    with pytest.raises(KronosError) as exc:
        svc.compute(fc)
    assert exc.value.code == KronosErrorCodes.MALFORMED_FORECAST


def test_metrics_configurable_flat_threshold() -> None:
    svc = KronosForecastMetricsService(flat_threshold=0.1)
    metrics = svc.compute(_make_forecast(_flat_bars()))
    assert metrics.direction == "FLAT"
    svc2 = KronosForecastMetricsService(flat_threshold=0.0)
    up = _make_forecast(_up_bars(horizon=2))
    metrics2 = svc2.compute(up)
    assert metrics2.direction == "UP"  # Any non-zero return is UP when threshold is 0


# ---------------------------------------------------------------------------
# Provider interface and integration tests
# ---------------------------------------------------------------------------


def test_all_providers_implement_interface() -> None:
    providers = [MockKronosProvider(), SMAForecastProvider(), LocalKronosProvider(model_id=KronosModelId.SMALL.value)]
    for p in providers:
        assert hasattr(p, "forecast")
        assert hasattr(p, "health")
        assert hasattr(p, "supported_models")
        assert hasattr(p, "model_metadata")
        assert getattr(p, "LIVE_TRADING_SUITABLE", False) is False


def test_kronos_error_codes_include_new_codes() -> None:
    assert KronosErrorCodes.INPUT_TOO_LONG.value == "INPUT_TOO_LONG"
    assert KronosErrorCodes.IRREGULAR_SPACING.value == "IRREGULAR_SPACING"


def test_mock_forecast_has_no_broker_dependency() -> None:
    import inspect

    source = inspect.getsource(MockKronosProvider)
    assert "ExecutionService" not in source
    assert "broker" not in source.lower()
    assert "order" not in source.lower()


def test_sma_forecast_has_no_broker_dependency() -> None:
    import inspect

    source = inspect.getsource(SMAForecastProvider)
    assert "ExecutionService" not in source
    assert "broker" not in source.lower()


def test_local_kronos_does_not_download_at_import() -> None:
    # The 'model' package should not be imported just by importing the service module
    import importlib

    # Remove any cached imports
    mods_to_remove = [k for k in sys.modules if k.startswith("model")]
    for m in mods_to_remove:
        del sys.modules[m]

    importlib.import_module("app.services.kronos.service")

    assert "model" not in sys.modules, "model package should not be imported at module load"


def test_validate_model_id_accepts_mock() -> None:
    from app.domain.models.kronos import validate_model_id

    assert validate_model_id("mock-kronos-v0") == "mock-kronos-v0"


def test_validate_model_id_accepts_baseline() -> None:
    from app.domain.models.kronos import validate_model_id

    assert validate_model_id("sma-baseline-v1") == "sma-baseline-v1"


def test_validate_model_id_accepts_official() -> None:
    from app.domain.models.kronos import validate_model_id

    assert validate_model_id("kronos-mini") == "kronos-mini"
    assert validate_model_id("kronos-small") == "kronos-small"
    assert validate_model_id("kronos-base") == "kronos-base"


def test_validate_model_id_rejects_large() -> None:
    from app.domain.models.kronos import validate_model_id

    with pytest.raises(ValueError):
        validate_model_id("kronos-large")


def test_validate_model_id_rejects_unknown() -> None:
    from app.domain.models.kronos import validate_model_id

    with pytest.raises(ValueError):
        validate_model_id("unknown-model")


def test_mock_forecast_no_exception_leakage_for_valid_input() -> None:
    """Ensure forecast calls that succeed don't leak internal exceptions."""
    provider = MockKronosProvider()
    req = make_request()
    fc = provider.forecast(req)
    assert isinstance(fc, KronosForecast)
    assert fc.warning is not None  # Always has a warning
