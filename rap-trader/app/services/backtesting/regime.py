"""Deterministic market-regime classification for backtesting evaluation windows.

Each evaluation window is classified into one of:

* ``trending_up``     — short SMA > long SMA and price trending upward
* ``trending_down``    — short SMA < long SMA and price trending downward
* ``range_bound``      — price stays within a narrow band, no clear trend
* ``high_volatility``  — volatility (std of returns) exceeds a high threshold
* ``low_volatility``   — volatility (std of returns) below a low threshold
* ``unknown``          — insufficient data to classify

Classification is deterministic: identical bar data always yields the same
regime.  Thresholds are configurable via the constructor.

No broker, execution, order, risk, or portfolio components are used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.models import MarketRegime


@dataclass(frozen=True)
class RegimeThresholds:
    """Configurable thresholds for regime classification.

    Attributes
    ----------
    short_window:
        Lookback window for the short moving average (default 5).
    long_window:
        Lookback window for the long moving average (default 20).
    low_vol_threshold:
        Standard deviation of returns below this → ``low_volatility`` (default 0.005).
    high_vol_threshold:
        Standard deviation of returns above this → ``high_volatility`` (default 0.03).
    trend_separation:
        Minimum ratio of short SMA to long SMA to be considered trending
        (default 0.01 = 1%).
    range_ratio:
        Maximum (high - low) / mean(close) ratio to be considered
        ``range_bound`` (default 0.05 = 5%).
    """

    short_window: int = 5
    long_window: int = 20
    low_vol_threshold: float = 0.005
    high_vol_threshold: float = 0.03
    trend_separation: float = 0.01
    range_ratio: float = 0.05


class MarketRegimeClassifier:
    """Deterministic market-regime classifier for evaluation windows.

    The classifier first checks volatility thresholds (high/low), then
    trend direction (short vs. long SMA), then range-bound behavior.

    Priority order:
    1. High volatility → ``high_volatility``
    2. Low volatility → ``low_volatility``
    3. Trending up (short > long by trend_separation) → ``trending_up``
    4. Trending down (short < long by trend_separation) → ``trending_down``
    5. Within range_ratio → ``range_bound``
    6. Otherwise → ``unknown``
    """

    def __init__(self, thresholds: RegimeThresholds | None = None) -> None:
        self.thresholds = thresholds or RegimeThresholds()

    def classify(self, bars: list[Any]) -> MarketRegime:
        """Classify a list of OHLCV bars (from a single evaluation window).

        Parameters
        ----------
        bars:
            List of objects with ``open``, ``high``, ``low``, ``close``
            attributes (e.g. ``OHLCVBar`` or ``ForecastBar``).

        Returns
        -------
        MarketRegime
            The classified regime.

        Priority order:
        1. High volatility → ``high_volatility``
        2. Trending up (short > long by trend_separation) → ``trending_up``
        3. Trending down (short < long by trend_separation) → ``trending_down``
        4. Low volatility → ``low_volatility``
        5. Within range_ratio → ``range_bound``
        6. Otherwise → ``unknown``
        """
        if not bars or len(bars) < 2:
            return MarketRegime.UNKNOWN

        closes = [b.close for b in bars]
        t = self.thresholds

        # --- Volatility (std of returns) ---
        returns: list[float] = []
        for i in range(1, len(closes)):
            if closes[i - 1] != 0:
                ret = (closes[i] - closes[i - 1]) / abs(closes[i - 1])
            else:
                ret = 0.0
            returns.append(ret)

        if len(returns) >= 1:
            mean_r = sum(returns) / len(returns)
            variance = sum((r - mean_r) ** 2 for r in returns) / len(returns)
            volatility = variance**0.5
        else:
            volatility = 0.0

        # --- High volatility check takes priority ---
        if volatility > t.high_vol_threshold:
            return MarketRegime.HIGH_VOLATILITY

        # --- Trend checks (short vs. long SMA) ---
        short_window = min(t.short_window, len(closes))
        long_window = min(t.long_window, len(closes))
        if long_window >= 2:
            short_sma = sum(closes[-short_window:]) / short_window
            long_sma = sum(closes[-long_window:]) / long_window

            if long_sma != 0:
                separation_ratio = (short_sma - long_sma) / abs(long_sma)
                if separation_ratio > t.trend_separation:
                    return MarketRegime.TRENDING_UP
                if separation_ratio < -t.trend_separation:
                    return MarketRegime.TRENDING_DOWN

        # --- Low volatility check ---
        if volatility < t.low_vol_threshold:
            return MarketRegime.LOW_VOLATILITY

        # --- Range-bound check ---
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]
        mean_close = sum(closes) / len(closes)
        if mean_close == 0:
            return MarketRegime.UNKNOWN
        price_range = (max(highs) - min(lows)) / abs(mean_close)

        if price_range <= t.range_ratio:
            return MarketRegime.RANGE_BOUND

        return MarketRegime.UNKNOWN
