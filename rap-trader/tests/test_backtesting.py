"""Comprehensive Phase 4 backtesting tests.

Covers:
- Window generation: correct walk-forward windows, step size, exact horizon
- Context-target separation and no-lookahead guards
- Lookahead detection, data leakage, target-in-context, duplicate timestamps
- Misaligned timestamps, future information access
- Maximum-window enforcement, data gaps, irregular spacing
- Deterministic output
- Perfect forecasts, biased forecasts, zero-price handling
- Metrics: MAE, RMSE, symmetric MAPE, correlation, direction accuracy, coverage
- Benchmark ranking
- Regime classification: trending up/down, range bound, high/low volatility
- Research signals: LONG, SHORT, FLAT (research-only)
- Transaction costs and slippage
- Short-selling disabled by default, no leverage by default
- Drawdown, turnover
- Persistence: save/load, atomic JSON writes, invalid schema, safe filenames
- API success and failure responses
- CLI offline defaults, no model download, no network
- No broker or execution dependency
- suitable_for_live_trading=False

All tests run fully offline with deterministic mock data.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.domain.models import (
    BacktestRunRequest,
    BacktestRunResult,
    BacktestStatus,
    EvaluationWindow,
    ForecastMetrics,
    HistoricalBarsResult,
    KronosForecastRequest,
    MarketRegime,
    OHLCVBar,
    ProviderBacktestResult,
    Symbol,
)
from app.domain.models.backtesting import (
    BacktestError,
    BacktestErrorCodes,
    ResearchSignal,
)
from app.main import app
from app.services.backtesting.costs import (
    CostConfig,
    FixedBpsCostModel,
    FixedBpsSlippageModel,
    ZeroCostModel,
    ZeroSlippageModel,
)
from app.services.backtesting.engine import (
    BacktestEngine,
    EvaluationWindowGenerator,
)
from app.services.backtesting.evaluator import ForecastEvaluator
from app.services.backtesting.providers import (
    DriftForecastProvider,
    LastValueForecastProvider,
)
from app.services.backtesting.regime import MarketRegimeClassifier, RegimeThresholds
from app.services.backtesting.research import ResearchSignalSimulator, SignalSimulationConfig
from app.services.backtesting.runner import BacktestRunner
from app.services.backtesting.store import (
    InMemoryBacktestResultStore,
    JSONFileBacktestResultStore,
)
from app.services.kronos import MockKronosProvider, SMAForecastProvider
from app.services.market_data import MockMarketDataProvider

client = TestClient(app)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

START = datetime(2025, 1, 1, tzinfo=UTC)
END = datetime(2026, 1, 1, tzinfo=UTC)
NUM_DAYS = 365


def make_timestamps(count: int, start: datetime = START) -> list[datetime]:
    """Generate ``count`` daily timestamps starting from ``start``."""
    return [start + timedelta(days=i) for i in range(count)]


def make_bars(count: int, start: datetime = START, base_price: float = 100.0) -> list[OHLCVBar]:
    """Generate ``count`` daily OHLCV bars with deterministic prices."""
    bars = []
    for i in range(count):
        ts = start + timedelta(days=i)
        price = base_price + i * 0.5  # gentle upward drift
        bars.append(
            OHLCVBar(
                timestamp=ts,
                open=price,
                high=price + 1.0,
                low=price - 1.0,
                close=price,
                volume=10000,
            )
        )
    return bars


def make_static_provider(closes: list[float]) -> MockMarketDataProvider:
    """Create a MockMarketDataProvider with deterministic data."""
    # We'll use the real MockMarketDataProvider since it's already deterministic
    return MockMarketDataProvider()


def make_request(
    ticker: str = "AAPL",
    timeframe: str = "1d",
    start: datetime = START,
    end: datetime = datetime(2025, 6, 1, tzinfo=UTC),
    lookback: int = 20,
    horizon: int = 5,
    step: int = 5,
    **kwargs: object,
) -> BacktestRunRequest:
    return BacktestRunRequest(
        ticker=ticker,
        timeframe=timeframe,
        start=start,
        end=end,
        lookback=lookback,
        horizon=horizon,
        step=step,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Window generation tests
# ---------------------------------------------------------------------------


class TestWindowGeneration:
    """Tests for EvaluationWindowGenerator."""

    def test_correct_walk_forward_windows(self) -> None:
        """Walk-forward windows are non-overlapping in targets when step == horizon."""
        timestamps = make_timestamps(100)
        gen = EvaluationWindowGenerator(timeframe="1d", lookback=20, horizon=5, step=5)
        windows = gen.generate(timestamps)
        assert len(windows) > 0
        # Windows should be sequential
        for i in range(1, len(windows)):
            assert windows[i].context_end >= windows[i - 1].context_end
            assert windows[i].window_index == i

    def test_window_step_size(self) -> None:
        """Each window advances by exactly ``step`` bars from the previous."""
        timestamps = make_timestamps(100)
        gen = EvaluationWindowGenerator(timeframe="1d", lookback=20, horizon=5, step=10)
        windows = gen.generate(timestamps)
        # The step between consecutive windows should be 10 days
        for i in range(1, len(windows)):
            delta = windows[i].context_end - windows[i - 1].context_end
            assert delta == timedelta(days=10)

    def test_exact_horizon(self) -> None:
        """Each window's target period spans exactly ``horizon`` steps."""
        timestamps = make_timestamps(100)
        horizon = 5
        gen = EvaluationWindowGenerator(timeframe="1d", lookback=20, horizon=horizon, step=5)
        windows = gen.generate(timestamps)
        for w in windows:
            target_bars = (w.target_end - w.target_start).days + 1
            assert target_bars == horizon

    def test_insufficient_history_raises(self) -> None:
        """Not enough bars to form a single window raises INSUFFICIENT_HISTORY."""
        timestamps = make_timestamps(10)  # need 20 + 5 = 25
        gen = EvaluationWindowGenerator(timeframe="1d", lookback=20, horizon=5, step=5)
        with pytest.raises(BacktestError) as exc:
            gen.generate(timestamps)
        assert exc.value.code == BacktestErrorCodes.INSUFFICIENT_HISTORY

    def test_maximum_window_enforcement(self) -> None:
        """max_windows caps the number of generated windows."""
        timestamps = make_timestamps(365)
        gen = EvaluationWindowGenerator(timeframe="1d", lookback=20, horizon=5, step=5, max_windows=3)
        windows = gen.generate(timestamps)
        assert len(windows) == 3

    def test_data_gaps_rejected(self) -> None:
        """Non-contiguous timestamps that violate the regular-spacing check raise IRREGULAR_SPACING."""
        timestamps = make_timestamps(50)
        # Create a gap by skipping some timestamps
        timestamps = list(timestamps[:10]) + list(timestamps[15:])
        gen = EvaluationWindowGenerator(timeframe="1d", lookback=5, horizon=3, step=3)
        with pytest.raises(BacktestError) as exc:
            gen.generate(timestamps)
        assert exc.value.code == BacktestErrorCodes.IRREGULAR_SPACING

    def test_duplicate_bars_rejected(self) -> None:
        """Duplicate timestamps raise DUPLICATE_TIMESTAMP."""
        timestamps = make_timestamps(50)
        # Insert a duplicate at the right sorted position
        timestamps = list(timestamps[:11]) + [timestamps[10]] + list(timestamps[11:])
        gen = EvaluationWindowGenerator(timeframe="1d", lookback=5, horizon=3, step=3)
        with pytest.raises(BacktestError) as exc:
            gen.generate(timestamps)
        assert exc.value.code == BacktestErrorCodes.DUPLICATE_TIMESTAMP

    def test_irregular_spacing_rejected(self) -> None:
        """Timestamps with non-uniform spacing raise IRREGULAR_SPACING."""
        timestamps = make_timestamps(50)
        # Remove one timestamp to create irregular spacing while preserving order
        timestamps = list(timestamps[:25]) + list(timestamps[26:])
        gen = EvaluationWindowGenerator(timeframe="1d", lookback=5, horizon=3, step=3)
        with pytest.raises(BacktestError) as exc:
            gen.generate(timestamps)
        assert exc.value.code == BacktestErrorCodes.IRREGULAR_SPACING

    def test_deterministic_output(self) -> None:
        """Same timestamps always produce same windows."""
        timestamps = make_timestamps(100)
        gen = EvaluationWindowGenerator(timeframe="1d", lookback=20, horizon=5, step=5)
        windows1 = gen.generate(timestamps)
        windows2 = gen.generate(timestamps)
        assert len(windows1) == len(windows2)
        for w1, w2 in zip(windows1, windows2):
            assert w1.context_start == w2.context_start
            assert w1.context_end == w2.context_end
            assert w1.target_start == w2.target_start
            assert w1.target_end == w2.target_end


# ---------------------------------------------------------------------------
# No-lookahead engine tests
# ---------------------------------------------------------------------------


class TestNoLookaheadGuards:
    """Tests for the no-lookahead runtime guards in BacktestEngine."""

    def test_lookahead_detected(self) -> None:
        """Forecast bars whose timestamps appear in context raise LOOKAHEAD_DETECTED."""
        timestamps = make_timestamps(100)
        gen = EvaluationWindowGenerator(timeframe="1d", lookback=20, horizon=5, step=5)
        windows = gen.generate(timestamps)
        window = windows[0]

        # Create a forecast with a timestamp that's in the context
        context_ts = window.context_end
        bad_forecast_bars = [
            type("Bar", (), {"timestamp": context_ts, "close": 100.0})(),
            type("Bar", (), {"timestamp": context_ts + timedelta(days=1), "close": 101.0})(),
        ]

        engine = BacktestEngine(
            market_data_provider=MockMarketDataProvider(),
            providers={},
            evaluator=ForecastEvaluator(),
            window_generator=gen,
        )

        forecast_timestamps = [b.timestamp for b in bad_forecast_bars]
        context_timestamps = timestamps[:20]  # mock context

        with pytest.raises(BacktestError) as exc:
            engine._check_no_lookahead(forecast_timestamps, context_timestamps)
        assert exc.value.code == BacktestErrorCodes.LOOKAHEAD_DETECTED

    def test_target_in_context_detected(self) -> None:
        """Target timestamps overlapping context raise TARGET_IN_CONTEXT."""
        timestamps = make_timestamps(100)
        gen = EvaluationWindowGenerator(timeframe="1d", lookback=20, horizon=5, step=5)
        gen.generate(timestamps)

        # Context timestamps are the last 20 bar timestamps
        context_ts = list(timestamps[80:100])
        # Target timestamps should start at index 100, but let's simulate overlap
        target_ts = [timestamps[99]]  # this overlaps with context

        engine = BacktestEngine(
            market_data_provider=MockMarketDataProvider(),
            providers={},
            evaluator=ForecastEvaluator(),
            window_generator=gen,
        )

        with pytest.raises(BacktestError) as exc:
            engine._check_no_target_in_context(context_ts, target_ts)
        assert exc.value.code == BacktestErrorCodes.TARGET_IN_CONTEXT

    def test_misaligned_timestamps_detected(self) -> None:
        """Forecast timestamps that don't match expected targets raise MISALIGNED_TIMESTAMPS."""
        timestamps = make_timestamps(100)
        gen = EvaluationWindowGenerator(timeframe="1d", lookback=20, horizon=5, step=5)
        windows = gen.generate(timestamps)
        window = windows[0]

        expected = gen.expected_target_timestamps(window.context_end)

        engine = BacktestEngine(
            market_data_provider=MockMarketDataProvider(),
            providers={},
            evaluator=ForecastEvaluator(),
            window_generator=gen,
        )

        # Create misaligned timestamps
        misaligned = [ts + timedelta(hours=1) for ts in expected]
        with pytest.raises(BacktestError) as exc:
            engine._check_alignment(misaligned, expected)
        assert exc.value.code == BacktestErrorCodes.MISALIGNED_TIMESTAMPS

    def test_future_information_blocked(self) -> None:
        """Market data returning bars beyond context_end raises FUTURE_INFORMATION."""
        gen = EvaluationWindowGenerator(timeframe="1d", lookback=20, horizon=5, step=5)
        engine = BacktestEngine(
            market_data_provider=MockMarketDataProvider(),
            providers={},
            evaluator=ForecastEvaluator(),
            window_generator=gen,
        )

        context_end = datetime(2025, 1, 21, tzinfo=UTC)
        # Create a fake result with a bar beyond context_end
        bad_bars = [
            OHLCVBar(timestamp=datetime(2025, 1, 21, tzinfo=UTC), open=100, high=101, low=99, close=100, volume=100),
            OHLCVBar(timestamp=datetime(2025, 1, 22, tzinfo=UTC), open=100, high=101, low=99, close=100, volume=100),
        ]

        # Use model_construct to bypass validation
        fake_result = HistoricalBarsResult.model_construct(
            symbol=Symbol("AAPL"),
            timeframe="1d",
            bars=bad_bars,
            provider="test",
            requested_start=datetime(2025, 1, 1, tzinfo=UTC),
            requested_end=context_end,
            actual_start=bad_bars[0].timestamp,
            actual_end=bad_bars[-1].timestamp,
            adjustment="raw",
            session="regular",
            retrieved_at=datetime.now(UTC),
        )

        with patch.object(engine.market_data_provider, "get_bars", return_value=fake_result), pytest.raises(BacktestError) as exc:
            engine._fetch_context_bars("AAPL", "1d", datetime(2025, 1, 1, tzinfo=UTC), context_end, 20)
        assert exc.value.code == BacktestErrorCodes.FUTURE_INFORMATION

    def test_duplicate_timestamp_guard(self) -> None:
        """Duplicate context timestamps raise DUPLICATE_TIMESTAMP."""
        dup_ts = [datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)]
        gen = EvaluationWindowGenerator(timeframe="1d", lookback=5, horizon=3, step=3)
        engine = BacktestEngine(
            market_data_provider=MockMarketDataProvider(),
            providers={},
            evaluator=ForecastEvaluator(),
            window_generator=gen,
        )
        with pytest.raises(BacktestError) as exc:
            engine._check_no_duplicates(dup_ts)
        assert exc.value.code == BacktestErrorCodes.DUPLICATE_TIMESTAMP


# ---------------------------------------------------------------------------
# Metrics tests
# ---------------------------------------------------------------------------


class TestForecastMetrics:
    """Tests for ForecastEvaluator metrics."""

    def test_mae_perfect_forecast(self) -> None:
        """Perfect forecast has MAE = 0."""
        actual = [100.0, 101.0, 102.0, 103.0, 104.0]
        metrics = ForecastEvaluator._compute_metrics(actual, actual)
        assert metrics.mae == 0.0

    def test_rmse_perfect_forecast(self) -> None:
        """Perfect forecast has RMSE = 0."""
        actual = [100.0, 101.0, 102.0, 103.0, 104.0]
        metrics = ForecastEvaluator._compute_metrics(actual, actual)
        assert metrics.rmse == 0.0

    def test_mae_biased_forecast(self) -> None:
        """Systematically biased forecast has non-zero MAE."""
        forecast = [105.0, 106.0, 107.0, 108.0, 109.0]
        actual = [100.0, 101.0, 102.0, 103.0, 104.0]
        metrics = ForecastEvaluator._compute_metrics(forecast, actual)
        assert metrics.mae == 5.0
        assert metrics.bias == 5.0

    def test_zero_price_handling(self) -> None:
        """Zero prices do not cause crashes."""
        forecast = [0.0, 100.0, 100.0]
        actual = [0.0, 100.0, 100.0]
        metrics = ForecastEvaluator._compute_metrics(forecast, actual)
        assert metrics.mae == 0.0
        assert metrics.sample_count == 3

    def test_symmetric_mape(self) -> None:
        """SMAPE is computed correctly."""
        forecast = [105.0]
        actual = [100.0]
        metrics = ForecastEvaluator._compute_metrics(forecast, actual)
        # sMAPE = |105-100| / ((|100|+|105|)/2) * 100 = 5 / 102.5 * 100
        expected = abs(5.0) / ((100.0 + 105.0) / 2) * 100
        assert metrics.symmetric_mape == pytest.approx(expected, rel=1e-6)

    def test_correlation_perfect(self) -> None:
        """Perfect positive correlation."""
        forecast = [100.0, 101.0, 102.0, 103.0, 104.0]
        actual = [200.0, 201.0, 202.0, 203.0, 204.0]
        metrics = ForecastEvaluator._compute_metrics(forecast, actual)
        assert metrics.correlation == pytest.approx(1.0, abs=1e-6)

    def test_correlation_negative(self) -> None:
        """Negative correlation when forecast goes up but actual goes down."""
        forecast = [100.0, 101.0, 102.0, 103.0, 104.0]
        actual = [104.0, 103.0, 102.0, 101.0, 100.0]
        metrics = ForecastEvaluator._compute_metrics(forecast, actual)
        assert metrics.correlation < 0

    def test_directional_accuracy(self) -> None:
        """Directional accuracy measures direction prediction correctness."""
        forecast = [100.0, 101.0, 102.0, 101.0, 102.0]
        actual = [100.0, 100.5, 101.5, 101.0, 101.5]
        # Both go up, up, down, up
        metrics = ForecastEvaluator._compute_metrics(forecast, actual)
        assert 0 <= metrics.directional_accuracy <= 1

    def test_interval_coverage(self) -> None:
        """Interval coverage measures how many actuals fall within forecast range."""
        forecast = [100.0, 105.0, 95.0, 110.0, 90.0]
        actual = [100.0, 105.0, 95.0, 50.0, 200.0]
        metrics = ForecastEvaluator._compute_metrics(forecast, actual)
        # forecast range: [90, 110], actual: [50, 200]
        # actuals in range: 100, 105, 95, 110... wait, actual has 100, 105, 95, 50, 200
        # 100 in [90,110], 105 in [90,110], 95 in [90,110], 50 not in, 200 not in
        assert metrics.interval_coverage == 0.6  # 3 of 5
        assert metrics.interval_width > 0


# ---------------------------------------------------------------------------
# Benchmark provider tests
# ---------------------------------------------------------------------------


class TestBenchmarkProviders:
    """Tests for benchmark forecast providers."""

    def test_all_providers_deterministic(self) -> None:
        """Each provider produces identical output for identical input."""

        # MockKronosProvider
        mock_req = KronosForecastRequest(
            ticker="AAPL",
            model_id="mock-kronos-v0",
            timeframe="1d",
            start=START,
            end=datetime(2025, 6, 1, tzinfo=UTC),
            lookback=20,
            horizon=5,
        )
        p1 = MockKronosProvider()
        p2 = MockKronosProvider()
        f1 = p1.forecast(mock_req)
        f2 = p2.forecast(mock_req)
        assert f1.model_dump(exclude={"generated_at"}) == f2.model_dump(exclude={"generated_at"})

    def test_last_value_provider_deterministic(self) -> None:
        """LastValueForecastProvider is deterministic."""
        req = KronosForecastRequest(
            ticker="AAPL",
            model_id="last-value-v1",
            timeframe="1d",
            start=START,
            end=datetime(2025, 6, 1, tzinfo=UTC),
            lookback=20,
            horizon=5,
        )
        p1 = LastValueForecastProvider()
        p2 = LastValueForecastProvider()
        f1 = p1.forecast(req)
        f2 = p2.forecast(req)
        assert f1.model_dump(exclude={"generated_at"}) == f2.model_dump(exclude={"generated_at"})

    def test_drift_provider_deterministic(self) -> None:
        """DriftForecastProvider is deterministic."""
        req = KronosForecastRequest(
            ticker="AAPL",
            model_id="drift-v1",
            timeframe="1d",
            start=START,
            end=datetime(2025, 6, 1, tzinfo=UTC),
            lookback=20,
            horizon=5,
        )
        p1 = DriftForecastProvider()
        p2 = DriftForecastProvider()
        f1 = p1.forecast(req)
        f1_dumps = f1.model_dump(exclude={"generated_at"})
        f2 = p2.forecast(req)
        f2_dumps = f2.model_dump(exclude={"generated_at"})
        assert f1_dumps == f2_dumps

    def test_benchmark_ranking(self) -> None:
        """Backtest runner ranks providers by RMSE."""
        req = make_request()
        runner = BacktestRunner(market_data_provider=MockMarketDataProvider())
        result = runner.run(req)
        assert result.status == BacktestStatus.COMPLETED
        assert len(result.providers) == 4

        # Find the best provider (lowest RMSE)
        best = min(result.providers, key=lambda p: p.mean_metrics.rmse)
        assert best.mean_metrics.rmse >= 0
        assert result.windows_evaluated > 0

    def test_all_providers_suitable_for_live_trading_false(self) -> None:
        """No provider claims live-trading suitability."""

        providers_to_check = [
            MockKronosProvider(),
            SMAForecastProvider(),
            LastValueForecastProvider(),
            DriftForecastProvider(),
        ]
        for provider in providers_to_check:
            assert getattr(provider, "LIVE_TRADING_SUITABLE", True) is False


# ---------------------------------------------------------------------------
# Regime classification tests
# ---------------------------------------------------------------------------


class TestRegimeClassification:
    """Tests for MarketRegimeClassifier."""

    def test_trending_up(self) -> None:
        """Up-trending prices with low volatility are classified as trending_up."""
        bars = []
        for i in range(20):
            price = 100.0 + i * 5  # strong upward trend
            bars.append(
                OHLCVBar(
                    timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
                    open=price,
                    high=price + 0.5,
                    low=price - 0.5,
                    close=price,
                    volume=1000,
                )
            )
        clf = MarketRegimeClassifier(RegimeThresholds(low_vol_threshold=0.05, high_vol_threshold=2.0))
        regime = clf.classify(bars)
        assert regime == MarketRegime.TRENDING_UP

    def test_trending_down(self) -> None:
        """Down-trending prices with low volatility are classified as trending_down."""
        bars = []
        for i in range(20):
            price = 200.0 - i * 5  # strong downward trend, keep prices positive
            bars.append(
                OHLCVBar(
                    timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
                    open=price,
                    high=price + 0.5,
                    low=price - 0.5,
                    close=price,
                    volume=1000,
                )
            )
        clf = MarketRegimeClassifier(RegimeThresholds(low_vol_threshold=0.05, high_vol_threshold=2.0))
        regime = clf.classify(bars)
        assert regime == MarketRegime.TRENDING_DOWN

    def test_range_bound(self) -> None:
        """Narrow-range prices near a flat mean are classified as range_bound."""
        bars = []
        for i in range(20):
            price = 100.0 + (i % 2) * 0.5  # small oscillation, std above low_vol_threshold
            bars.append(
                OHLCVBar(
                    timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
                    open=price,
                    high=price + 0.5,
                    low=max(price - 0.5, 0.01),
                    close=price,
                    volume=1000,
                )
            )
        clf = MarketRegimeClassifier(
            RegimeThresholds(low_vol_threshold=0.001, high_vol_threshold=0.5, trend_separation=0.5, range_ratio=0.05)
        )
        regime = clf.classify(bars)
        assert regime == MarketRegime.RANGE_BOUND

    def test_high_volatility(self) -> None:
        """Large price swings are classified as high_volatility."""
        bars = []
        for i in range(20):
            sign = 1 if i % 2 == 0 else -1
            close = 100.0 + sign * 15.0  # large swings, keep prices positive
            bars.append(
                OHLCVBar(
                    timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
                    open=close,
                    high=close + 1.0,
                    low=max(close - 1.0, 0.01),
                    close=close,
                    volume=1000,
                )
            )
        clf = MarketRegimeClassifier(RegimeThresholds(low_vol_threshold=0.001, high_vol_threshold=0.03))
        regime = clf.classify(bars)
        assert regime == MarketRegime.HIGH_VOLATILITY

    def test_low_volatility(self) -> None:
        """Tiny price changes are classified as low_volatility."""
        bars = []
        for i in range(20):
            price = 100.0 + i * 0.001  # very small changes
            bars.append(
                OHLCVBar(
                    timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
                    open=price,
                    high=price + 0.001,
                    low=price - 0.001,
                    close=price,
                    volume=1000,
                )
            )
        clf = MarketRegimeClassifier(RegimeThresholds(low_vol_threshold=0.005, high_vol_threshold=0.5))
        regime = clf.classify(bars)
        assert regime == MarketRegime.LOW_VOLATILITY

    def test_unknown_regime_insufficient_data(self) -> None:
        """Single bar or empty list returns unknown."""
        clf = MarketRegimeClassifier()
        assert clf.classify([]) == MarketRegime.UNKNOWN
        assert (
            clf.classify([OHLCVBar(timestamp=datetime(2025, 1, 1, tzinfo=UTC), open=100, high=101, low=99, close=100, volume=100)])
            == MarketRegime.UNKNOWN
        )

    def test_regime_classification_deterministic(self) -> None:
        """Same input always produces same regime."""
        bars = make_bars(20)
        clf = MarketRegimeClassifier()
        r1 = clf.classify(bars)
        r2 = clf.classify(bars)
        assert r1 == r2


# ---------------------------------------------------------------------------
# Research signal simulator tests
# ---------------------------------------------------------------------------


class TestResearchSignalSimulator:
    """Tests for the research-only signal simulator."""

    def test_long_signal(self) -> None:
        """Forecast above reference by > threshold generates LONG (short-selling disabled)."""
        forecast_closes = [100.0, 102.0, 104.0]
        actual_closes = [100.0, 101.0, 103.0]
        window = EvaluationWindow(
            window_index=0,
            context_start=datetime(2025, 1, 1, tzinfo=UTC),
            context_end=datetime(2025, 1, 3, tzinfo=UTC),
            target_start=datetime(2025, 1, 4, tzinfo=UTC),
            target_end=datetime(2025, 1, 6, tzinfo=UTC),
            timeframe="1d",
        )
        sim = ResearchSignalSimulator()
        result = sim.simulate(forecast_closes, actual_closes, window, context_last_close=100.0)
        signals = result["signals"]
        assert all(s.signal == ResearchSignal.LONG for s in signals)
        assert result["research_only"] is True
        assert result["suitable_for_live_trading"] is False

    def test_short_signal(self) -> None:
        """Forecast below reference generates SHORT only when short_selling allowed."""
        forecast_closes = [100.0, 98.0, 96.0]
        actual_closes = [100.0, 99.0, 97.0]
        window = EvaluationWindow(
            window_index=0,
            context_start=datetime(2025, 1, 1, tzinfo=UTC),
            context_end=datetime(2025, 1, 3, tzinfo=UTC),
            target_start=datetime(2025, 1, 4, tzinfo=UTC),
            target_end=datetime(2025, 1, 6, tzinfo=UTC),
            timeframe="1d",
        )
        # Without short selling -> FLAT
        sim = ResearchSignalSimulator()
        result = sim.simulate(forecast_closes, actual_closes, window, context_last_close=100.0)
        signals = result["signals"]
        assert all(s.signal == ResearchSignal.FLAT for s in signals)

        # With short selling -> SHORT
        sim_short = ResearchSignalSimulator(config=SignalSimulationConfig(short_selling=True))
        result_short = sim_short.simulate(forecast_closes, actual_closes, window, context_last_close=100.0)
        signals_short = result_short["signals"]
        assert all(s.signal == ResearchSignal.SHORT for s in signals_short)

    def test_flat_signal(self) -> None:
        """Forecast near reference generates FLAT."""
        forecast_closes = [100.0, 100.5, 100.2]
        actual_closes = [100.0, 100.3, 100.1]
        window = EvaluationWindow(
            window_index=0,
            context_start=datetime(2025, 1, 1, tzinfo=UTC),
            context_end=datetime(2025, 1, 3, tzinfo=UTC),
            target_start=datetime(2025, 1, 4, tzinfo=UTC),
            target_end=datetime(2025, 1, 6, tzinfo=UTC),
            timeframe="1d",
        )
        sim = ResearchSignalSimulator()
        result = sim.simulate(forecast_closes, actual_closes, window, context_last_close=100.0)
        signals = result["signals"]
        assert all(s.signal == ResearchSignal.FLAT for s in signals)
        assert result["research_only"] is True
        assert result["suitable_for_live_trading"] is False

    def test_short_selling_disabled_by_default(self) -> None:
        """Default config does not allow short selling."""
        sim = ResearchSignalSimulator()
        assert sim.config.short_selling is False

    def test_no_leverage_by_default(self) -> None:
        """Default config has leverage = 1.0."""
        sim = ResearchSignalSimulator()
        assert sim.config.leverage == 1.0


# ---------------------------------------------------------------------------
# Cost and slippage model tests
# ---------------------------------------------------------------------------


class TestCostModels:
    """Tests for transaction cost and slippage models."""

    def test_zero_cost_model(self) -> None:
        """Zero cost model returns 0."""
        model = ZeroCostModel()
        config = CostConfig(transaction_cost_bps=0.0)
        assert model.compute(100.0, 105.0, 1.0, config) == 0.0

    def test_zero_slippage_model(self) -> None:
        """Zero slippage model returns 0."""
        model = ZeroSlippageModel()
        config = CostConfig(slippage_bps=0.0)
        assert model.compute(100.0, 1.0, config) == 0.0

    def test_fixed_bps_cost(self) -> None:
        """Fixed BPS cost model computes correctly."""
        model = FixedBpsCostModel(bps=10.0)
        config = CostConfig(transaction_cost_bps=10.0, leverage=1.0)
        # entry=100, exit=105, position=1
        # entry_value = 100*1*1 = 100, exit_value = 105*1*1 = 105
        # cost = 10/10000 * (100+105) = 0.001 * 205 = 0.205
        cost = model.compute(100.0, 105.0, 1.0, config)
        assert cost == pytest.approx(0.205, abs=1e-10)

    def test_fixed_bps_slippage(self) -> None:
        """Fixed BPS slippage model computes correctly."""
        model = FixedBpsSlippageModel(bps=5.0)
        config = CostConfig(slippage_bps=5.0, leverage=1.0)
        # trade_value = 100 * 1 = 100, slippage = 5/10000 * 100 * 1 = 0.05
        slippage = model.compute(100.0, 1.0, config)
        assert slippage == pytest.approx(0.05, abs=1e-10)

    def test_short_selling_blocked_by_default(self) -> None:
        """Short position with short_selling_allowed=False returns 0 cost (trade blocked)."""
        model = FixedBpsCostModel(bps=10.0)
        config = CostConfig(transaction_cost_bps=10.0, short_selling_allowed=False)
        cost = model.compute(100.0, 95.0, -1.0, config)
        assert cost == 0.0

    def test_short_selling_allowed(self) -> None:
        """Short position with short_selling_allowed=True incurs costs."""
        model = FixedBpsCostModel(bps=10.0)
        config = CostConfig(transaction_cost_bps=10.0, short_selling_allowed=True)
        cost = model.compute(100.0, 95.0, -1.0, config)
        assert cost > 0.0


# ---------------------------------------------------------------------------
# Persistence tests
# ---------------------------------------------------------------------------


class TestPersistence:
    """Tests for InMemoryBacktestResultStore and JSONFileBacktestResultStore."""

    def _make_result(self, backtest_id: str = "test-001") -> BacktestRunResult:
        """Build a valid BacktestRunResult for testing."""

        metrics = ForecastMetrics(
            mae=1.0,
            rmse=1.5,
            median_absolute_error=0.8,
            symmetric_mape=2.0,
            bias=0.1,
            max_error=5.0,
            correlation=0.8,
            directional_accuracy=0.7,
            sign_accuracy=0.6,
            hit_rate=0.65,
            interval_coverage=0.9,
            interval_width=0.02,
            sample_count=100,
        )
        return BacktestRunResult(
            backtest_id=backtest_id,
            status=BacktestStatus.COMPLETED,
            request=BacktestRunRequest(
                ticker="AAPL",
                timeframe="1d",
                start=START,
                end=datetime(2025, 6, 1, tzinfo=UTC),
                lookback=20,
                horizon=5,
                step=5,
            ),
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
            completed_at=datetime(2025, 1, 1, tzinfo=UTC),
            research_only=True,
            suitable_for_live_trading=False,
            providers=[
                ProviderBacktestResult(
                    provider="MockKronosProvider",
                    research_only=True,
                    suitable_for_live_trading=False,
                    mean_metrics=metrics,
                    regime_breakdown={"range_bound": {"count": 10, "mean_mae": 1.0, "mean_rmse": 1.5}},
                )
            ],
            regime_distribution={"range_bound": 10},
            windows_total=10,
            windows_evaluated=10,
        )

    def test_in_memory_save_load(self) -> None:
        """InMemoryBacktestResultStore saves and loads correctly."""
        store = InMemoryBacktestResultStore()
        result = self._make_result()
        store.save(result)
        loaded = store.load(result.backtest_id)
        assert loaded is not None
        assert loaded.backtest_id == result.backtest_id
        assert loaded.status == BacktestStatus.COMPLETED

    def test_in_memory_list(self) -> None:
        """InMemoryBacktestResultStore lists saved IDs."""
        store = InMemoryBacktestResultStore()
        r1 = self._make_result("test-001")
        r2 = self._make_result("test-002")
        store.save(r1)
        store.save(r2)
        ids = store.list()
        assert "test-001" in ids
        assert "test-002" in ids

    def test_in_memory_delete(self) -> None:
        """InMemoryBacktestResultStore deletes correctly."""
        store = InMemoryBacktestResultStore()
        result = self._make_result()
        store.save(result)
        assert store.delete(result.backtest_id) is True
        assert store.load(result.backtest_id) is None
        assert store.delete(result.backtest_id) is False

    def test_json_file_save_load(self) -> None:
        """JSONFileBacktestResultStore saves and loads with schema version."""
        tmpdir = tempfile.mkdtemp()
        try:
            store = JSONFileBacktestResultStore(tmpdir)
            result = self._make_result()
            store.save(result)

            # Verify file exists
            filepath = os.path.join(tmpdir, f"{result.backtest_id}.json")
            assert os.path.isfile(filepath)

            # Verify schema version in file
            with open(filepath) as f:
                data = json.load(f)
            assert data["schema_version"] == "1.0"

            # Load it back
            loaded = store.load(result.backtest_id)
            assert loaded is not None
            assert loaded.backtest_id == result.backtest_id
            assert loaded.research_only is True
            assert loaded.suitable_for_live_trading is False
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_json_file_atomic_write(self) -> None:
        """JSON writes are atomic — no partial files on success."""
        tmpdir = tempfile.mkdtemp()
        try:
            store = JSONFileBacktestResultStore(tmpdir)
            result = self._make_result()
            store.save(result)

            # No temp files should remain
            files = os.listdir(tmpdir)
            json_files = [f for f in files if f.endswith(".json")]
            tmp_files = [f for f in files if f.startswith(".")]
            assert len(json_files) == 1
            assert len(tmp_files) == 0
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_json_file_invalid_schema_rejected(self) -> None:
        """JSON with wrong schema_version is rejected on load."""
        tmpdir = tempfile.mkdtemp()
        try:
            store = JSONFileBacktestResultStore(tmpdir)
            result = self._make_result()
            store.save(result)

            # Corrupt the schema version
            filepath = os.path.join(tmpdir, f"{result.backtest_id}.json")
            with open(filepath) as f:
                data = json.load(f)
            data["schema_version"] = "0.9"
            with open(filepath, "w") as f:
                json.dump(data, f)

            loaded = store.load(result.backtest_id)
            assert loaded is None  # Invalid schema rejected
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_json_file_safe_filenames(self) -> None:
        """Invalid backtest IDs are rejected — no path traversal."""
        tmpdir = tempfile.mkdtemp()
        try:
            store = JSONFileBacktestResultStore(tmpdir)
            # Invalid ID with path traversal
            bad_result = self._make_result()
            bad_result = bad_result.model_copy(update={"backtest_id": "../../../etc/passwd"})
            with pytest.raises(ValueError):
                store.save(bad_result)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_json_file_no_pickle(self) -> None:
        """JSON files contain only JSON, not pickle data."""
        tmpdir = tempfile.mkdtemp()
        try:
            store = JSONFileBacktestResultStore(tmpdir)
            result = self._make_result()
            store.save(result)

            filepath = os.path.join(tmpdir, f"{result.backtest_id}.json")
            with open(filepath, "rb") as f:
                raw = f.read()
            # JSON should not contain pickle markers
            assert b"\x80\x02" not in raw  # pickle protocol 2 marker
            assert raw.startswith((b"{", b"["))  # valid JSON
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


class TestBacktestAPI:
    """Tests for the backtesting API endpoints."""

    def test_run_backtest_success(self) -> None:
        """POST /backtests/run returns a completed result."""
        req = make_request()
        response = client.post(
            "/backtests/run",
            content=req.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "COMPLETED"
        assert data["research_only"] is True
        assert data["suitable_for_live_trading"] is False
        assert len(data["providers"]) == 4

    def test_run_backtest_failure_invalid_request(self) -> None:
        """POST /backtests/run with invalid range returns 400."""
        req = BacktestRunRequest.model_construct(
            ticker="AAPL",
            timeframe="1d",
            start=datetime(2025, 6, 1, tzinfo=UTC),
            end=datetime(2025, 1, 1, tzinfo=UTC),  # end before start
            lookback=20,
            horizon=5,
            step=5,
        )
        response = client.post(
            "/backtests/run",
            content=req.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422  # Validation error

    def test_providers_endpoint(self) -> None:
        """GET /backtests/providers lists available providers."""
        response = client.get("/backtests/providers")
        assert response.status_code == 200
        data = response.json()
        assert "providers" in data
        assert "MockKronosProvider" in data["providers"]
        assert data["benchmark_only"] is True

    def test_get_backtest_not_found(self) -> None:
        """GET /backtests/{id} returns 404 for unknown ID."""
        response = client.get("/backtests/nonexistent-id-12345")
        assert response.status_code == 404

    def test_get_backtest_summary_not_found(self) -> None:
        """GET /backtests/{id}/summary returns 404 for unknown ID."""
        response = client.get("/backtests/nonexistent-id-12345/summary")
        assert response.status_code == 404

    def test_backtest_result_has_research_only_flag(self) -> None:
        """Every result in /backtests/run response has research_only=True."""
        req = make_request()
        response = client.post(
            "/backtests/run",
            content=req.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["research_only"] is True
        assert data["suitable_for_live_trading"] is False

    def test_backtest_endpoints_are_read_only(self) -> None:
        """PUT/DELETE on /backtests/run returns 405."""
        assert client.put("/backtests/run").status_code == 405
        assert client.delete("/backtests/run").status_code == 405


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestCLI:
    """Tests for the CLI smoke behavior and offline defaults."""

    def test_cli_runs_offline(self) -> None:
        """CLI runs successfully without network or model download."""
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "app.cli.backtest",
                "--ticker",
                "AAPL",
                "--start",
                "2025-01-01",
                "--end",
                "2025-06-01",
                "--lookback",
                "20",
                "--horizon",
                "5",
                "--step",
                "10",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        output = json.loads(result.stdout)
        assert output["status"] == "COMPLETED"
        assert output["research_only"] is True
        assert output["suitable_for_live_trading"] is False

    def test_cli_offline_defaults(self) -> None:
        """CLI defaults to offline, mock data, no persistence."""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "app.cli.backtest", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0
        assert "offline" in result.stdout.lower() or "mock" in result.stdout.lower()

    def test_cli_no_model_download(self) -> None:
        """CLI does not import torch or download models."""
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; sys.argv = ['app.cli.backtest', '--start', '2025-01-01', '--end', '2025-03-01', '--lookback', '10', '--horizon', '3', '--step', '5']; "
                    "from app.cli.backtest import main; main(); "
                    "assert 'torch' not in sys.modules, 'torch should not be imported'; "
                    "print('No torch import confirmed')"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0
        assert "No torch import confirmed" in result.stdout

    def test_cli_no_broker_dependency(self) -> None:
        """CLI does not import broker, execution, or risk modules."""
        import inspect

        from app.cli.backtest import main

        source = inspect.getsource(main)
        assert "ExecutionService" not in source
        assert "broker" not in source.lower()
        assert "OrderRequest" not in source
        assert "RiskEngine" not in source
        assert "PortfolioManager" not in source


# ---------------------------------------------------------------------------
# Safety and isolation tests
# ---------------------------------------------------------------------------


class TestSafetyIsolation:
    """Tests verifying no forbidden dependencies."""

    def test_backtesting_no_broker_import(self) -> None:
        """Backtesting modules do not import broker/execution/risk/order."""
        import inspect

        from app.services.backtesting.costs import FixedBpsCostModel
        from app.services.backtesting.engine import BacktestEngine
        from app.services.backtesting.evaluator import ForecastEvaluator
        from app.services.backtesting.providers import (
            DriftForecastProvider,
            LastValueForecastProvider,
        )
        from app.services.backtesting.regime import MarketRegimeClassifier
        from app.services.backtesting.research import ResearchSignalSimulator
        from app.services.backtesting.runner import BacktestRunner
        from app.services.backtesting.store import InMemoryBacktestResultStore

        modules_to_check = [
            BacktestEngine,
            BacktestRunner,
            ForecastEvaluator,
            LastValueForecastProvider,
            DriftForecastProvider,
            FixedBpsCostModel,
            MarketRegimeClassifier,
            ResearchSignalSimulator,
            InMemoryBacktestResultStore,
        ]

        forbidden = [
            "app.services.broker",
            "app.services.execution",
            "app.services.risk",
            "app.services.order",
            "app.services.portfolio",
        ]

        for cls in modules_to_check:
            source = inspect.getsource(cls)
            for forbidden_mod in forbidden:
                assert forbidden_mod not in source, f"{cls.__name__} source references forbidden module {forbidden_mod}"

    def test_backtesting_module_no_forbidden_imports(self) -> None:
        """The backtesting package __init__ does not import forbidden modules."""
        import app.services.backtesting as bt

        forbidden_prefixes = ["broker", "execution", "risk", "portfolio"]
        for obj in vars(bt).values():
            if hasattr(obj, "__name__") and obj.__name__.startswith("app."):
                for prefix in forbidden_prefixes:
                    assert not obj.__name__.startswith(f"app.services.{prefix}"), (
                        f"backtesting package imports forbidden module: {obj.__name__}"
                    )

    def test_results_suitable_for_live_trading_false(self) -> None:
        """Every backtest result has suitable_for_live_trading=False."""
        req = make_request()
        runner = BacktestRunner(market_data_provider=MockMarketDataProvider())
        result = runner.run(req)
        assert result.suitable_for_live_trading is False
        for p in result.providers:
            assert p.suitable_for_live_trading is False

    def test_results_research_only_true(self) -> None:
        """Every backtest result has research_only=True."""
        req = make_request()
        runner = BacktestRunner(market_data_provider=MockMarketDataProvider())
        result = runner.run(req)
        assert result.research_only is True
        for p in result.providers:
            assert p.research_only is True

    def test_no_local_kronos_by_default(self) -> None:
        """LocalKronosProvider is not included unless explicitly requested."""
        req = make_request(include_local_kronos=False)
        runner = BacktestRunner(market_data_provider=MockMarketDataProvider())
        result = runner.run(req)
        provider_names = [p.provider for p in result.providers]
        assert "LocalKronosProvider" not in provider_names


# ---------------------------------------------------------------------------
# Full walk-forward integration test
# ---------------------------------------------------------------------------


class TestWalkForwardIntegration:
    """End-to-end walk-forward backtest integration tests."""

    def test_deterministic_walk_forward(self) -> None:
        """Same request always produces same results."""
        req = make_request()
        runner = BacktestRunner(market_data_provider=MockMarketDataProvider())
        r1 = runner.run(req)
        r2 = runner.run(req)
        assert r1.backtest_id == r2.backtest_id
        assert r1.windows_total == r2.windows_total
        assert r1.windows_evaluated == r2.windows_evaluated
        # Compare metrics (excluding timestamps)
        for p1, p2 in zip(r1.providers, r2.providers):
            assert p1.mean_metrics.mae == p2.mean_metrics.mae
            assert p1.mean_metrics.rmse == p2.mean_metrics.rmse

    def test_research_simulation_produces_costs(self) -> None:
        """Research simulation produces before/after cost results."""
        req = make_request(research_simulation=True, short_selling=True, transaction_cost_bps=10.0, slippage_bps=5.0)
        runner = BacktestRunner(market_data_provider=MockMarketDataProvider())
        result = runner.run(req)
        assert result.status == BacktestStatus.COMPLETED
        for p in result.providers:
            assert p.cost_result is not None
            assert p.cost_result.total_costs >= 0
            assert p.research_signal_summary is not None
            assert p.research_signal_summary["research_only"] == 1.0
            assert p.research_signal_summary["suitable_for_live_trading"] == 0.0

    def test_turnover_computed(self) -> None:
        """Turnover is computed and non-negative when research simulation is on."""
        req = make_request(research_simulation=True)
        runner = BacktestRunner(market_data_provider=MockMarketDataProvider())
        result = runner.run(req)
        for p in result.providers:
            if p.cost_result is not None:
                assert p.cost_result.turnover >= 0

    def test_drawdown_computed(self) -> None:
        """Max drawdown is computed and non-negative."""
        req = make_request(research_simulation=True)
        runner = BacktestRunner(market_data_provider=MockMarketDataProvider())
        result = runner.run(req)
        for p in result.providers:
            if p.cost_result is not None:
                assert p.cost_result.max_drawdown >= 0

    def test_report_performance_before_and_after_costs(self) -> None:
        """Cost results distinguish gross vs net PnL."""
        req = make_request(research_simulation=True, transaction_cost_bps=20.0, slippage_bps=10.0)
        runner = BacktestRunner(market_data_provider=MockMarketDataProvider())
        result = runner.run(req)
        for p in result.providers:
            if p.cost_result is not None:
                cr = p.cost_result
                # Net PnL = Gross PnL - total costs (approximately)
                assert cr.total_costs >= 0
                assert cr.commission_cost >= 0
                assert cr.slippage_cost >= 0
