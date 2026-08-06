"""No-lookahead walk-forward backtesting engine.

This module implements:

* ``EvaluationWindowGenerator`` — produces non-overlapping evaluation windows
  for a walk-forward backtest.  Each window has a *context* period (history
  available to the forecast) and a *target* period (future bars compared
  against the forecast).  Windows are generated with a fixed ``step`` so
  that no future bar from one window leaks into the context of a subsequent
  window.

* ``BacktestEngine`` — orchestrates the walk-forward loop, enforces hard
  no-lookahead invariants at runtime, and delegates forecast generation to
  ``KronosForecastProvider`` instances and evaluation to
  ``ForecastEvaluator``.

Design guarantees (enforced at runtime, not just documented):

1. **Target bars never appear in context.**  Before a forecast is evaluated,
   every forecast bar timestamp is checked against the context bar timestamps.
   Any overlap raises ``BacktestError(LOOKAHEAD_DETECTED)``.

2. **Misaligned timestamps are rejected.**  Forecast bar timestamps must
   match the expected target timestamps exactly.

3. **Future information is blocked.**  The engine receives historical bars
   only up to ``context_end``; it never requests or receives bars beyond
   that point from the market-data provider.

4. **Duplicate timestamps in any input raise immediately.**

5. **Maximum-window enforcement.**  ``max_windows`` caps the total number of
   windows to prevent unbounded runtimes.

All comparisons use UTC-normalized timestamps.  No broker, execution, order,
risk, or portfolio dependencies are imported or called.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from app.domain.models import (
    BacktestError,
    BacktestErrorCodes,
    EvaluationWindow,
    HistoricalBarsRequest,
    HistoricalBarsResult,
    Timeframe,
)
from app.domain.models.backtesting import BacktestRunRequest
from app.services.market_data.base import MarketDataProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TIMEFRAME_DELTAS: dict[Timeframe, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}


def _timeframe_step(timeframe: Timeframe) -> timedelta:
    """Return the canonical step for a timeframe."""
    return TIMEFRAME_DELTAS[timeframe]


def _generate_future_timestamps(last_timestamp: datetime, horizon: int, timeframe: Timeframe) -> list[datetime]:
    """Generate *exactly* ``horizon`` future timestamps starting one step after ``last_timestamp``.

    This mirrors the logic used by ``MockKronosProvider`` and
    ``KronosInputAdapter`` so that forecast bar timestamps align precisely
    with actual future bar timestamps.
    """
    step = _timeframe_step(timeframe)
    return [last_timestamp + step * (i + 1) for i in range(horizon)]


# ---------------------------------------------------------------------------
# Window generation
# ---------------------------------------------------------------------------


class EvaluationWindowGenerator:
    """Generate walk-forward evaluation windows without overlap or leakage.

    Parameters
    ----------
    timeframe:
        Bar timeframe (e.g. ``"1d"``).
    lookback:
        Number of historical bars available to the forecast provider in
        each window.
    horizon:
        Number of future target bars per window.
    step:
        Number of bars to advance between consecutive windows.  Must be
        ``>= 1``.  A step equal to ``horizon`` produces non-overlapping
        target windows; smaller steps produce overlapping targets (still
        safe because the *context* never extends past ``context_end``).
    max_windows:
        Hard cap on the number of windows.  ``None`` means no cap
        (callers should set a sane default).
    """

    def __init__(
        self,
        timeframe: Timeframe,
        lookback: int,
        horizon: int,
        step: int,
        max_windows: int | None = None,
    ) -> None:
        if lookback < 1:
            raise ValueError("lookback must be >= 1")
        if horizon < 1:
            raise ValueError("horizon must be >= 1")
        if step < 1:
            raise ValueError("step must be >= 1")
        if max_windows is not None and max_windows < 1:
            raise ValueError("max_windows must be >= 1")

        self.timeframe = timeframe
        self.lookback = lookback
        self.horizon = horizon
        self.step = step
        self.max_windows = max_windows

    def generate(self, all_timestamps: Sequence[datetime]) -> list[EvaluationWindow]:
        """Generate evaluation windows from a sorted sequence of bar timestamps.

        Parameters
        ----------
        all_timestamps:
            Chronologically ordered, unique, UTC-aware datetime objects
            representing every bar available for backtesting.

        Returns
        -------
        list[EvaluationWindow]
            One window per step position.  Each window's context contains
            exactly ``lookback`` bars ending at ``context_end``, and the
            target period spans exactly ``horizon`` steps starting at
            ``context_end + one_step``.

        Raises
        ------
        BacktestError
            If timestamps are unsorted, contain duplicates, have irregular
            spacing, or if the total number of windows exceeds
            ``max_windows``.
        """
        timestamps = list(all_timestamps)
        if not timestamps:
            raise BacktestError(
                BacktestErrorCodes.INSUFFICIENT_HISTORY,
                "No timestamps provided for window generation",
            )

        # Validate chronological order and uniqueness
        for i in range(1, len(timestamps)):
            if timestamps[i] <= timestamps[i - 1]:
                if timestamps[i] == timestamps[i - 1]:
                    raise BacktestError(
                        BacktestErrorCodes.DUPLICATE_TIMESTAMP,
                        "Duplicate timestamp detected in input data",
                        internal_detail=f"timestamp: {timestamps[i].isoformat()}",
                    )
                raise BacktestError(
                    BacktestErrorCodes.INVALID_REQUEST,
                    "Timestamps are not in chronological order",
                )

        # Validate regular spacing
        step_td = _timeframe_step(self.timeframe)
        for i in range(1, len(timestamps)):
            delta = timestamps[i] - timestamps[i - 1]
            if abs(delta - step_td) > timedelta(seconds=1):
                raise BacktestError(
                    BacktestErrorCodes.IRREGULAR_SPACING,
                    f"Irregular spacing at index {i}: expected {step_td}, got {delta}",
                    internal_detail=f"expected={step_td}, actual={delta}",
                )

        windows: list[EvaluationWindow] = []

        # The first context window needs `lookback` bars.
        # context_end = timestamps[lookback - 1]
        # target_start = timestamps[lookback]
        # target_end = timestamps[lookback + horizon - 1]
        # The last full window must have enough bars: lookback + horizon - 1
        # total needed = lookback + horizon
        min_bars = self.lookback + self.horizon
        if len(timestamps) < min_bars:
            raise BacktestError(
                BacktestErrorCodes.INSUFFICIENT_HISTORY,
                f"Insufficient history: {len(timestamps)} bars available, "
                f"need at least {min_bars} (lookback={self.lookback} + horizon={self.horizon})",
            )

        # Walk forward by `step` bars each iteration.
        # context_end_index is the index of the last context bar.
        context_end_index = self.lookback - 1
        while True:
            target_start_index = context_end_index + 1
            target_end_index = context_end_index + self.horizon

            if target_end_index >= len(timestamps):
                break

            window = EvaluationWindow(
                window_index=len(windows),
                context_start=timestamps[context_end_index - self.lookback + 1],
                context_end=timestamps[context_end_index],
                target_start=timestamps[target_start_index],
                target_end=timestamps[target_end_index],
                timeframe=self.timeframe,
            )
            windows.append(window)

            # Check max_windows
            if self.max_windows is not None and len(windows) >= self.max_windows:
                break

            # Advance by step
            context_end_index += self.step

        if not windows:
            raise BacktestError(
                BacktestErrorCodes.INSUFFICIENT_HISTORY,
                "No complete evaluation windows could be generated from the available history",
            )

        return windows

    def expected_target_timestamps(self, context_end: datetime) -> list[datetime]:
        """Return the exact target timestamps that should appear in a forecast.

        This is the canonical reference used by the engine to verify
        forecast alignment — forecast bars whose timestamps do not match
        this list will be rejected as misaligned.
        """
        return _generate_future_timestamps(context_end, self.horizon, self.timeframe)


# ---------------------------------------------------------------------------
# No-lookahead engine
# ---------------------------------------------------------------------------


class BacktestEngine:
    """Walk-forward backtesting engine with hard runtime no-lookahead guards.

    The engine receives a ``MarketDataProvider``, a list of
    ``KronosForecastProvider`` instances, and a
    ``ForecastEvaluator``.  For each evaluation window it:

    1. Requests only historical bars whose timestamps are ``<= context_end``
       from the market-data provider.
    2. Issues a ``KronosForecastRequest`` bounded by ``context_end`` so the
       forecast provider can never see beyond ``context_end``.
    3. Receives a ``KronosForecast`` containing future bars.
    4. **Runtime guard:** verifies that no forecast bar timestamp appears in
       the context bar timestamps (``LOOKAHEAD_DETECTED``).
    5. **Runtime guard:** verifies that forecast bar timestamps exactly match
       the expected target timestamps (``MISALIGNED_TIMESTAMPS``).
    6. Extracts the actual target bars from the full dataset (bars at the
       target timestamps).
    7. Delegates comparison to ``ForecastEvaluator``.
    """

    def __init__(
        self,
        market_data_provider: MarketDataProvider,
        providers: dict[str, Any],
        evaluator: Any,
        window_generator: EvaluationWindowGenerator,
    ) -> None:
        self.market_data_provider = market_data_provider
        self.providers = providers
        self.evaluator = evaluator
        self.window_generator = window_generator

    def _fetch_context_bars(
        self,
        ticker: str,
        timeframe: Timeframe,
        context_start: datetime,
        context_end: datetime,
        lookback: int,
    ) -> HistoricalBarsResult:
        """Fetch only the bars up to and including ``context_end``.

        The request is bounded so that the market-data provider never
        returns bars beyond ``context_end`` — this is the primary
        future-information guard.
        """
        request = HistoricalBarsRequest(
            symbol=ticker,
            timeframe=timeframe,
            start=context_start,
            end=context_end,
            limit=lookback,
            adjustment="raw",
            session="regular",
        )
        result = self.market_data_provider.get_bars(request)
        # Post-hoc safety: reject any bar that is strictly after context_end
        leaked = [b for b in result.bars if b.timestamp > context_end]
        if leaked:
            raise BacktestError(
                BacktestErrorCodes.FUTURE_INFORMATION,
                "Market data provider returned bars beyond the context end boundary",
                internal_detail=f"leaked timestamps: {[b.timestamp.isoformat() for b in leaked]}",
            )
        return result

    @staticmethod
    def _check_no_lookahead(forecast_timestamps: list[datetime], context_timestamps: list[datetime]) -> None:
        """Verify no forecast bar timestamp appears in the context bars."""
        context_set = set(context_timestamps)
        for ts in forecast_timestamps:
            if ts in context_set:
                raise BacktestError(
                    BacktestErrorCodes.LOOKAHEAD_DETECTED,
                    f"Forecast bar timestamp {ts.isoformat()} appears in the context window",
                    internal_detail=f"timestamp: {ts.isoformat()}",
                )

    @staticmethod
    def _check_alignment(forecast_timestamps: list[datetime], expected: list[datetime]) -> None:
        """Verify forecast timestamps match the expected target timestamps exactly."""
        if len(forecast_timestamps) != len(expected):
            raise BacktestError(
                BacktestErrorCodes.MISALIGNED_TIMESTAMPS,
                f"Forecast length {len(forecast_timestamps)} does not match expected horizon {len(expected)}",
            )
        for fts, ets in zip(forecast_timestamps, expected):
            if fts != ets:
                raise BacktestError(
                    BacktestErrorCodes.MISALIGNED_TIMESTAMPS,
                    f"Forecast timestamp {fts.isoformat()} does not match expected {ets.isoformat()}",
                )

    @staticmethod
    def _check_no_target_in_context(context_timestamps: list[datetime], target_timestamps: list[datetime]) -> None:
        """Verify target bar timestamps do not overlap context bar timestamps."""
        context_set = set(context_timestamps)
        for ts in target_timestamps:
            if ts in context_set:
                raise BacktestError(
                    BacktestErrorCodes.TARGET_IN_CONTEXT,
                    f"Target bar timestamp {ts.isoformat()} appears in the context window",
                )

    @staticmethod
    def _check_no_duplicates(timestamps: list[datetime]) -> None:
        """Reject duplicate timestamps in any list of timestamps."""
        seen: set[datetime] = set()
        for ts in timestamps:
            if ts in seen:
                raise BacktestError(
                    BacktestErrorCodes.DUPLICATE_TIMESTAMP,
                    f"Duplicate timestamp detected: {ts.isoformat()}",
                )
            seen.add(ts)

    def _extract_target_bars(
        self,
        full_timestamps: list[datetime],
        full_bars: list[Any],
        target_timestamps: list[datetime],
    ) -> list[Any]:
        """Extract the actual target bars from the full dataset by timestamp.

        This requires the full dataset to be available (e.g. from a
        ``MockMarketDataProvider``) — but the engine only passes
        ``context_end`` to the provider, so the provider returns at most
        ``context_end``.  For backtesting we need the *actual* future bars;
        these must come from the same deterministic source but must be
        retrieved via a separate bounded request *after* context_end is
        known, and the target bars must not be visible to the forecast
        provider.
        """
        # Build a timestamp->bar lookup
        lookup: dict[datetime, Any] = {}
        for bar, ts in zip(full_bars, full_timestamps):
            lookup[ts] = bar

        target_bars: list[Any] = []
        missing: list[str] = []
        for ts in target_timestamps:
            bar = lookup.get(ts)
            if bar is None:
                missing.append(ts.isoformat())
                continue
            target_bars.append(bar)

        if missing:
            raise BacktestError(
                BacktestErrorCodes.DATA_GAP,
                f"Target bars missing for timestamps: {missing[:5]}",
                internal_detail=f"missing_count={len(missing)}",
            )

        return target_bars

    def evaluate_window(
        self,
        provider_name: str,
        provider: Any,
        request: BacktestRunRequest,
        window: EvaluationWindow,
        full_bars: list[Any],
        full_timestamps: list[datetime],
        context_last_close: float | None = None,
    ) -> Any:
        """Evaluate a single provider on a single window.

        Returns the metrics dict produced by ``ForecastEvaluator``.
        """
        # --- Step 1: Fetch context bars (bounded by context_end) ---
        context_result = self._fetch_context_bars(
            request.ticker,
            request.timeframe,
            window.context_start,
            window.context_end,
            request.lookback,
        )
        context_bars = context_result.bars
        context_timestamps = [b.timestamp for b in context_bars]

        # --- Runtime guards on context ---
        self._check_no_duplicates(context_timestamps)
        expected_targets = self.window_generator.expected_target_timestamps(window.context_end)
        self._check_no_duplicates(expected_targets)
        self._check_no_target_in_context(context_timestamps, expected_targets)

        # --- Step 2: Build a KronosForecastRequest bounded to context ---
        from app.domain.models import KronosForecastRequest

        forecast_request = KronosForecastRequest(
            ticker=request.ticker,
            model_id=request.model_id if hasattr(request, "model_id") else _provider_model_id(provider_name),
            timeframe=request.timeframe,
            start=window.context_start,
            end=window.context_end,
            lookback=request.lookback,
            horizon=request.horizon,
        )

        # --- Step 3: Generate forecast ---
        forecast = provider.forecast(forecast_request)

        # --- Step 4: No-lookahead guard ---
        forecast_timestamps = [b.timestamp for b in forecast.bars]
        self._check_no_lookahead(forecast_timestamps, context_timestamps)

        # --- Step 5: Alignment guard ---
        self._check_alignment(forecast_timestamps, expected_targets)

        # --- Step 6: Extract actual target bars ---
        target_bars = self._extract_target_bars(full_timestamps, full_bars, expected_targets)

        # --- Step 7: Evaluate ---
        return self.evaluator.evaluate(forecast, target_bars, window, context_last_close)


def _provider_model_id(provider_name: str) -> str:
    """Map a provider name to a model_id string for the forecast request."""
    mapping = {
        "MockKronosProvider": "mock-kronos-v0",
        "SMAForecastProvider": "sma-baseline-v1",
        "LastValueForecastProvider": "last-value-v1",
        "DriftForecastProvider": "drift-v1",
    }
    return mapping.get(provider_name, "unknown")
