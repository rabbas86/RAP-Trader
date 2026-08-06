"""High-level backtest runner.

The ``BacktestRunner`` orchestrates the full walk-forward backtest:

1. Fetches the full historical dataset from a ``MarketDataProvider`` (only
   if ``include_local_kronos`` is set, a ``LocalKronosProvider`` may be
   supplied; otherwise only mock/benchmark providers are used).
2. Generates evaluation windows using ``EvaluationWindowGenerator``.
3. For each window, runs each provider through ``BacktestEngine``.
4. Aggregates metrics, regime distribution, and (optionally) research
   signal simulation.
5. Returns a ``BacktestRunResult``.

The runner enforces that:

* All providers have ``LIVE_TRADING_SUITABLE = False``.
* ``LocalKronosProvider`` is only used when ``include_local_kronos=True``.
* Every result carries ``research_only=True`` and
  ``suitable_for_live_trading=False``.
* No broker, execution, order, risk, or portfolio components are called.

No broker, execution, order, risk, or portfolio components are used.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from app.domain.models import (
    BacktestError,
    BacktestErrorCodes,
    BacktestRunRequest,
    BacktestRunResult,
    BacktestStatus,
    BenchmarkProvider,
    ForecastMetrics,
    HistoricalBarsRequest,
    HistoricalBarsResult,
    MarketDataError,
    ProviderBacktestResult,
    Symbol,
)
from app.domain.models.backtesting import CostResult
from app.services.backtesting.engine import BacktestEngine, EvaluationWindowGenerator
from app.services.backtesting.evaluator import ForecastEvaluator
from app.services.backtesting.providers import (
    DriftForecastProvider,
    LastValueForecastProvider,
)
from app.services.backtesting.regime import MarketRegimeClassifier
from app.services.backtesting.research import ResearchSignalSimulator, SignalSimulationConfig
from app.services.kronos import KronosForecastProvider, MockKronosProvider, SMAForecastProvider
from app.services.market_data import MarketDataProvider, MockMarketDataProvider


class BacktestRunner:
    """Orchestrates a complete walk-forward backtest.

    Parameters
    ----------
    market_data_provider:
        The ``MarketDataProvider`` used to fetch historical bars.  Defaults
        to ``MockMarketDataProvider`` (deterministic, offline).
    """

    def __init__(
        self,
        market_data_provider: MarketDataProvider | None = None,
    ) -> None:
        self.market_data_provider = market_data_provider or MockMarketDataProvider()

    def run(self, request: BacktestRunRequest) -> BacktestRunResult:
        """Execute a walk-forward backtest.

        Returns a ``BacktestRunResult`` with aggregated metrics for each
        provider.
        """
        backtest_id = self._generate_id(request)

        result = BacktestRunResult(
            backtest_id=backtest_id,
            status=BacktestStatus.RUNNING,
            request=request,
            created_at=datetime.now(UTC),
            research_only=True,
            suitable_for_live_trading=False,
            providers=[],
            regime_distribution={},
            windows_total=0,
            windows_evaluated=0,
        )

        try:
            # --- Step 1: Fetch historical bars ---
            full_result = self._fetch_full_history(request)
            all_bars = full_result.bars
            all_timestamps = [b.timestamp for b in all_bars]

            # --- Step 2: Generate windows ---
            generator = EvaluationWindowGenerator(
                timeframe=request.timeframe,
                lookback=request.lookback,
                horizon=request.horizon,
                step=request.step,
                max_windows=request.max_windows,
            )
            windows = generator.generate(all_timestamps)

            # --- Step 3: Build providers ---
            providers = self._build_providers(request)

            # --- Step 4: Build evaluator ---
            research_config = SignalSimulationConfig(
                short_selling=request.short_selling,
                leverage=request.leverage,
                transaction_cost_bps=request.transaction_cost_bps,
                slippage_bps=request.slippage_bps,
            )
            research_sim = None
            if request.research_simulation:
                research_sim = ResearchSignalSimulator(config=research_config)

            evaluator = ForecastEvaluator(
                regime_classifier=MarketRegimeClassifier(),
                research_simulator=research_sim,
            )

            # --- Step 5: Run each provider ---
            provider_results: list[ProviderBacktestResult] = []
            regime_counts: dict[str, int] = {}
            all_window_metrics: dict[str, list[Any]] = {name: [] for name in providers}

            for window in windows:
                # Extract the context last close for research simulation
                context_idx = None
                for i, ts in enumerate(all_timestamps):
                    if ts == window.context_end:
                        context_idx = i
                        break
                context_last_close = all_bars[context_idx].close if context_idx is not None and context_idx >= 0 else None

                for provider_name, provider in providers.items():
                    engine = BacktestEngine(
                        market_data_provider=self.market_data_provider,
                        providers=providers,
                        evaluator=evaluator,
                        window_generator=generator,
                    )

                    try:
                        window_result = engine.evaluate_window(
                            provider_name=provider_name,
                            provider=provider,
                            request=request,
                            window=window,
                            full_bars=all_bars,
                            full_timestamps=all_timestamps,
                            context_last_close=context_last_close,
                        )
                    except BacktestError:
                        # Skip windows that fail — they don't contribute to metrics
                        continue

                    all_window_metrics[provider_name].append(window_result)

                    # Track regime
                    regime = window_result["regime"]
                    regime_counts[regime.value] = regime_counts.get(regime.value, 0) + 1

                    # Track research simulation if enabled
                    if "signal_simulation" in window_result:
                        pass  # already captured in window_result

            # --- Step 6: Aggregate ---
            for provider_name in providers:
                window_results = all_window_metrics[provider_name]
                if not window_results:
                    provider_results.append(
                        ProviderBacktestResult(
                            provider=provider_name,
                            research_only=True,
                            suitable_for_live_trading=False,
                            mean_metrics=self._empty_metrics(),
                            regime_breakdown={},
                            warning="No windows evaluated successfully",
                        )
                    )
                    continue

                metrics_list = [r["metrics"] for r in window_results]
                mean_metrics = ForecastEvaluator.aggregate_metrics(metrics_list)

                regime_breakdown: dict[str, dict[str, float | int]] = {}
                for r in window_results:
                    regime = r["regime"]
                    rb = regime_breakdown.setdefault(regime.value, {})
                    rb["count"] = rb.get("count", 0) + 1
                    m = r["metrics"]
                    rb["mean_mae"] = float(rb.get("mean_mae", 0.0)) + m.mae
                    rb["mean_rmse"] = float(rb.get("mean_rmse", 0.0)) + m.rmse

                # Average per-regime metrics
                for rb in regime_breakdown.values():
                    count = rb.get("count", 1)
                    rb["mean_mae"] = round(float(rb["mean_mae"]) / count, 10)
                    rb["mean_rmse"] = round(float(rb["mean_rmse"]) / count, 10)

                # Research simulation aggregation
                cost_result = None
                signal_summary = None
                if request.research_simulation:
                    total_gross = 0.0
                    total_net = 0.0
                    total_costs = 0.0
                    total_turnover = 0.0
                    all_returns_before: list[float] = []
                    all_returns_after: list[float] = []
                    all_positions: list[float] = []
                    for r in window_results:
                        if "signal_simulation" in r:
                            sim = r["signal_simulation"]
                            cr = sim["cost_result"]
                            total_gross += cr.gross_pnl
                            total_net += cr.net_pnl
                            total_costs += cr.total_costs
                            total_turnover += cr.turnover
                            all_returns_before.extend(sim["returns_before_cost"])
                            all_returns_after.extend(sim["returns_after_cost"])
                            all_positions.extend(sim["positions"])

                    cost_result = CostResult(
                        gross_pnl=round(total_gross, 10),
                        total_costs=round(total_costs, 10),
                        net_pnl=round(total_net, 10),
                        turnover=round(total_turnover, 10),
                        commission_cost=0.0,
                        slippage_cost=0.0,
                        max_drawdown=0.0,
                        short_selling_allowed=request.short_selling,
                        leverage=request.leverage,
                    )

                    # Compute max drawdown from returns_after
                    if all_returns_after:
                        cumulative = 1.0
                        peak = 1.0
                        max_dd = 0.0
                        for r in all_returns_after:
                            cumulative *= 1 + r
                            peak = max(peak, cumulative)
                            if peak > 0:
                                max_dd = max(max_dd, (peak - cumulative) / peak)
                        cost_result.max_drawdown = round(max_dd, 10)
                        cost_result.commission_cost = round(total_costs, 10)
                        cost_result.slippage_cost = round(total_costs, 10)

                    signal_summary = {
                        "total_gross_pnl": round(total_gross, 10),
                        "total_net_pnl": round(total_net, 10),
                        "total_costs": round(total_costs, 10),
                        "turnover": round(total_turnover, 10),
                        "num_positions": len([p for p in all_positions if p != 0]),
                        "research_only": 1.0,
                        "suitable_for_live_trading": 0.0,
                    }

                provider_results.append(
                    ProviderBacktestResult(
                        provider=provider_name,
                        research_only=True,
                        suitable_for_live_trading=False,
                        mean_metrics=mean_metrics,
                        regime_breakdown=regime_breakdown,
                        cost_result=cost_result,
                        research_signal_summary=signal_summary,
                    )
                )

            # --- Step 7: Build final result ---
            result = BacktestRunResult(
                backtest_id=backtest_id,
                status=BacktestStatus.COMPLETED,
                request=request,
                created_at=result.created_at,
                completed_at=datetime.now(UTC),
                schema_version="1.0",
                research_only=True,
                suitable_for_live_trading=False,
                providers=provider_results,
                regime_distribution=regime_counts,
                windows_total=len(windows),
                windows_evaluated=len(windows),
            )

        except BacktestError as exc:
            result.status = BacktestStatus.FAILED
            result.error = exc.safe_message
            result.completed_at = datetime.now(UTC)
            result.providers = []
            result.regime_distribution = {}
        except MarketDataError as exc:
            result.status = BacktestStatus.FAILED
            result.error = exc.safe_message
            result.completed_at = datetime.now(UTC)
            result.providers = []
            result.regime_distribution = {}
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fetch_full_history(self, request: BacktestRunRequest) -> HistoricalBarsResult:
        """Fetch the full historical dataset for the backtest range.

        This fetches bars from the market-data provider bounded by
        ``request.start`` and ``request.end``.  The provider must return
        only historical data — the backtest engine itself enforces that
        each window's context never exceeds ``context_end``.
        """
        md_request = HistoricalBarsRequest(
            symbol=Symbol(request.ticker),
            timeframe=request.timeframe,
            start=request.start,
            end=request.end,
            adjustment="raw",
            session="regular",
        )
        result = self.market_data_provider.get_bars(md_request)
        if not result.bars:
            raise BacktestError(
                BacktestErrorCodes.INSUFFICIENT_HISTORY,
                "Market data provider returned no bars",
            )
        return result

    def _build_providers(self, request: BacktestRunRequest) -> dict[str, KronosForecastProvider]:
        """Build the set of forecast providers to evaluate.

        Always includes MockKronosProvider, SMAForecastProvider,
        LastValueForecastProvider, and DriftForecastProvider.

        LocalKronosProvider is included only when
        ``request.include_local_kronos`` is ``True``.
        """
        providers: dict[str, KronosForecastProvider] = {
            BenchmarkProvider.MOCK.value: MockKronosProvider(),
            BenchmarkProvider.SMA.value: SMAForecastProvider(
                provider=self.market_data_provider,
            ),
            BenchmarkProvider.LAST_VALUE.value: LastValueForecastProvider(
                provider=self.market_data_provider,
            ),
            BenchmarkProvider.DRIFT.value: DriftForecastProvider(
                provider=self.market_data_provider,
            ),
        }

        if request.include_local_kronos:
            from app.services.kronos import LocalKronosProvider

            providers["LocalKronosProvider"] = LocalKronosProvider(
                model_id="kronos-small",
                device="cpu",
                offline_only=True,
            )

        # Verify no provider claims live-trading suitability
        for name, provider in providers.items():
            if getattr(provider, "LIVE_TRADING_SUITABLE", True):
                raise BacktestError(
                    BacktestErrorCodes.INVALID_REQUEST,
                    f"Provider {name} claims LIVE_TRADING_SUITABLE=True; backtesting rejects live-trading providers",
                )

        return providers

    @staticmethod
    def _generate_id(request: BacktestRunRequest) -> str:
        """Generate a deterministic backtest ID from the request."""
        material = json.dumps(request.model_dump(mode="json"), sort_keys=True, default=str)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _empty_metrics() -> ForecastMetrics:
        """Return a zero-valued ForecastMetrics for providers with no results."""
        return ForecastMetrics(
            mae=0.0,
            rmse=0.0,
            median_absolute_error=0.0,
            symmetric_mape=0.0,
            bias=0.0,
            max_error=0.0,
            correlation=0.0,
            directional_accuracy=0.0,
            sign_accuracy=0.0,
            hit_rate=0.0,
            interval_coverage=0.0,
            interval_width=0.0,
            sample_count=0,
        )
