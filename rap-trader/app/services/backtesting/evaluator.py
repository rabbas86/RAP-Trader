"""Forecast evaluation and metrics computation.

The ``ForecastEvaluator`` compares forecast OHLCV bars against actual
future OHLCV bars for a single evaluation window and computes a
deterministic set of metrics.

All metrics are computed on the *close* price series, which is the
standard convention for forecast-versus-actual evaluation.  The evaluator
also classifies the market regime for the window and optionally runs the
research signal simulator.

No broker, execution, order, risk, or portfolio components are used.
"""

from __future__ import annotations

from typing import Any

from app.domain.models import (
    BacktestError,
    BacktestErrorCodes,
    EvaluationWindow,
    ForecastMetrics,
)
from app.services.backtesting.regime import MarketRegimeClassifier
from app.services.backtesting.research import ResearchSignalSimulator


class ForecastEvaluator:
    """Evaluate forecast quality for a single evaluation window.

    Parameters
    ----------
    regime_classifier:
        ``MarketRegimeClassifier`` used to label the window.
    research_simulator:
        Optional ``ResearchSignalSimulator`` used when research signal
        simulation is requested.
    """

    def __init__(
        self,
        regime_classifier: MarketRegimeClassifier | None = None,
        research_simulator: ResearchSignalSimulator | None = None,
    ) -> None:
        self.regime_classifier = regime_classifier or MarketRegimeClassifier()
        self.research_simulator = research_simulator

    # ------------------------------------------------------------------
    # Core evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        forecast: Any,
        target_bars: list[Any],
        window: EvaluationWindow,
        context_last_close: float | None = None,
    ) -> dict[str, Any]:
        """Evaluate ``forecast`` bars against ``target_bars`` for ``window``.

        Returns a dict with ``metrics``, ``regime``, and optional
        ``signal_simulation`` keys.
        """
        forecast_closes = [b.close for b in forecast.bars]
        target_closes = [b.close for b in target_bars]

        if len(forecast_closes) != len(target_closes):
            raise BacktestError(
                BacktestErrorCodes.MISALIGNED_TIMESTAMPS,
                f"Forecast length {len(forecast_closes)} does not match target length {len(target_closes)}",
            )

        metrics = self._compute_metrics(forecast_closes, target_closes)
        regime = self.regime_classifier.classify(target_bars)

        result: dict[str, Any] = {
            "metrics": metrics,
            "regime": regime,
        }

        if self.research_simulator is not None:
            signal_result = self.research_simulator.simulate(
                forecast_closes,
                target_closes,
                window,
                context_last_close=context_last_close,
            )
            result["signal_simulation"] = signal_result

        return result

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_metrics(forecast: list[float], actual: list[float]) -> ForecastMetrics:
        """Compute the full metric set for a pair of forecast/actual close series.

        All metrics are deterministic functions of the inputs.

        Metrics computed:
            - mae: Mean Absolute Error
            - rmse: Root Mean Square Error
            - median_absolute_error
            - symmetric_mape: symmetric Mean Absolute Percentage Error
            - bias: mean(forecast - actual)
            - max_error: maximum absolute error
            - correlation: Pearson correlation (None if undefined)
            - directional_accuracy: fraction of direction predictions correct
            - sign_accuracy: fraction of sign(pred - actual_prev) == sign(actual - actual_prev)
            - hit_rate: fraction of times forecast direction matches actual direction
            - interval_coverage: fraction of actuals within forecast [min, max] band
            - interval_width: mean(forecast_high - forecast_low) / mean(actual close)
            - sample_count
        """
        n = len(forecast)
        if n == 0:
            raise BacktestError(
                BacktestErrorCodes.INVALID_REQUEST,
                "Cannot compute metrics on empty series",
            )

        errors = [f - a for f, a in zip(forecast, actual)]
        abs_errors = [abs(e) for e in errors]

        # MAE
        mae = sum(abs_errors) / n

        # RMSE
        mse = sum(e * e for e in errors) / n
        rmse = mse**0.5

        # Median absolute error
        sorted_abs = sorted(abs_errors)
        if n % 2 == 1:
            median_ae = sorted_abs[n // 2]
        else:
            median_ae = (sorted_abs[n // 2 - 1] + sorted_abs[n // 2]) / 2

        # Symmetric MAPE
        smape_components: list[float] = []
        for f, a in zip(forecast, actual):
            denom = (abs(a) + abs(f)) / 2
            if denom == 0:
                smape_components.append(0.0)
            else:
                smape_components.append(abs(f - a) / denom * 100)
        smape = sum(smape_components) / n if smape_components else 0.0

        # Bias
        bias = sum(errors) / n

        # Max error
        max_error = max(abs_errors)

        # Correlation (Pearson)
        mean_f = sum(forecast) / n
        mean_a = sum(actual) / n
        cov = sum((f - mean_f) * (a - mean_a) for f, a in zip(forecast, actual))
        var_f = sum((f - mean_f) ** 2 for f in forecast)
        var_a = sum((a - mean_a) ** 2 for a in actual)
        denom = (var_f * var_a) ** 0.5
        correlation: float | None
        if denom == 0:
            correlation = None
        else:
            correlation = cov / denom
            # Clamp to [-1, 1] due to floating-point
            correlation = max(-1.0, min(1.0, correlation))

        # Directional accuracy: did forecast predict the direction of actual change?
        # Compare forecast[i] vs forecast[i-1] to actual[i] vs actual[i-1]
        directional_correct = 0
        directional_total = 0
        sign_correct = 0
        sign_total = 0
        for i in range(1, n):
            actual_dir = actual[i] - actual[i - 1]
            forecast_dir = forecast[i] - forecast[i - 1]

            if actual_dir > 0:
                actual_sign = 1
            elif actual_dir < 0:
                actual_sign = -1
            else:
                actual_sign = 0

            if forecast_dir > 0:
                forecast_sign = 1
            elif forecast_dir < 0:
                forecast_sign = -1
            else:
                forecast_sign = 0

            # Directional accuracy: same sign (including flat==flat)
            if forecast_sign == actual_sign:
                directional_correct += 1
            directional_total += 1

            # Sign accuracy: did the error sign match? (forecast - actual) sign
            # Sign accuracy here is defined as: sign(forecast - actual_prev) == sign(actual - actual_prev)
            err = forecast[i] - actual[i]
            if err != 0 and actual_dir != 0:
                if (err > 0) == (actual_dir > 0):
                    sign_correct += 1
                sign_total += 1
            elif actual_dir == 0:
                # If actual didn't move, sign accuracy counts as correct if forecast also didn't move
                sign_total += 1
                if forecast_dir == 0:
                    sign_correct += 1

        directional_accuracy = directional_correct / directional_total if directional_total > 0 else 1.0
        sign_accuracy = sign_correct / sign_total if sign_total > 0 else 1.0

        # Hit rate: fraction of times forecast direction matches actual direction
        # (same as directional_accuracy but only counting non-flat actuals)
        hit_correct = 0
        hit_total = 0
        for i in range(1, n):
            actual_dir = actual[i] - actual[i - 1]
            if actual_dir == 0:
                continue
            forecast_dir = forecast[i] - forecast[i - 1]
            if (forecast_dir > 0) == (actual_dir > 0):
                hit_correct += 1
            hit_total += 1
        hit_rate = hit_correct / hit_total if hit_total > 0 else 1.0

        # Interval coverage: fraction of actuals within the forecast band
        # The forecast band is [min(forecast), max(forecast)] per bar — but we
        # need per-bar high/low. Since we only have closes, we use the
        # forecast range as a proxy: any actual within [min_forecast, max_forecast].
        f_min = min(forecast)
        f_max = max(forecast)
        coverage = sum(1 for a in actual if f_min <= a <= f_max) / n

        # Interval width: mean(forecast_high - forecast_low) / mean(actual close)
        # As a proxy with close-only data, use (f_max - f_min) / mean(actual)
        mean_actual = mean_a if n > 0 else 0.0
        interval_width = (f_max - f_min) / mean_actual if mean_actual != 0 else 0.0

        return ForecastMetrics(
            mae=round(mae, 10),
            rmse=round(rmse, 10),
            median_absolute_error=round(median_ae, 10),
            symmetric_mape=round(smape, 10),
            bias=round(bias, 10),
            max_error=round(max_error, 10),
            correlation=correlation,
            directional_accuracy=round(directional_accuracy, 10),
            sign_accuracy=round(sign_accuracy, 10),
            hit_rate=round(hit_rate, 10),
            interval_coverage=round(coverage, 10),
            interval_width=round(interval_width, 10),
            sample_count=n,
        )

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    @staticmethod
    def aggregate_metrics(all_metrics: list[ForecastMetrics]) -> ForecastMetrics:
        """Aggregate per-window metrics into a single mean over all windows."""
        if not all_metrics:
            raise BacktestError(
                BacktestErrorCodes.INVALID_REQUEST,
                "Cannot aggregate empty metrics list",
            )

        n = len(all_metrics)
        mae = sum(m.mae for m in all_metrics) / n
        rmse = sum(m.rmse for m in all_metrics) / n
        median_ae = sum(m.median_absolute_error for m in all_metrics) / n
        smape = sum(m.symmetric_mape for m in all_metrics) / n
        bias = sum(m.bias for m in all_metrics) / n
        max_error = sum(m.max_error for m in all_metrics) / n
        corrs = [m.correlation for m in all_metrics if m.correlation is not None]
        correlation = sum(corrs) / len(corrs) if corrs else None
        dir_acc = sum(m.directional_accuracy for m in all_metrics) / n
        sign_acc = sum(m.sign_accuracy for m in all_metrics) / n
        hit_rate = sum(m.hit_rate for m in all_metrics) / n
        coverage = sum(m.interval_coverage for m in all_metrics) / n
        width = sum(m.interval_width for m in all_metrics) / n
        sample_count = sum(m.sample_count for m in all_metrics)

        return ForecastMetrics(
            mae=round(mae, 10),
            rmse=round(rmse, 10),
            median_absolute_error=round(median_ae, 10),
            symmetric_mape=round(smape, 10),
            bias=round(bias, 10),
            max_error=round(max_error, 10),
            correlation=correlation,
            directional_accuracy=round(dir_acc, 10),
            sign_accuracy=round(sign_acc, 10),
            hit_rate=round(hit_rate, 10),
            interval_coverage=round(coverage, 10),
            interval_width=round(width, 10),
            sample_count=sample_count,
        )
