"""Momentum features extracted from the Phase 6 Technical Analyst."""

from itertools import pairwise

from app.domain.models.features import FeatureScalar
from app.domain.models.market_data import OHLCVBar
from app.services.features.generators.trend import TrendFeatureGenerator


class MomentumFeatureGenerator:
    version = "1.0.0"
    minimum_bars = 35

    @staticmethod
    def roc(values: list[float], period: int) -> float:
        if period <= 0 or len(values) <= period:
            raise ValueError("insufficient values for ROC")
        return (values[-1] / values[-period - 1] - 1) * 100

    @staticmethod
    def rsi(values: list[float], period: int) -> float:
        if period <= 0 or len(values) < period + 1:
            raise ValueError("insufficient values for RSI")
        deltas = [current - previous for previous, current in pairwise(values)]
        gains = [max(value, 0.0) for value in deltas]
        losses = [max(-value, 0.0) for value in deltas]
        gain, loss = sum(gains[:period]) / period, sum(losses[:period]) / period
        for current_gain, current_loss in zip(gains[period:], losses[period:], strict=True):
            gain = (gain * (period - 1) + current_gain) / period
            loss = (loss * (period - 1) + current_loss) / period
        return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)

    @staticmethod
    def macd(values: list[float], fast: int, slow: int, signal: int) -> tuple[float, float, float]:
        if fast >= slow or len(values) < slow:
            raise ValueError("insufficient values for MACD")
        fast_values = TrendFeatureGenerator.ema_series(values, fast)
        slow_values = TrendFeatureGenerator.ema_series(values, slow)
        line = [a - b for a, b in zip(fast_values[slow - fast :], slow_values, strict=True)]
        signal_value = TrendFeatureGenerator.ema(line, signal) if len(line) >= signal else sum(line) / len(line)
        return line[-1], signal_value, line[-1] - signal_value

    @classmethod
    def generate(cls, bars: list[OHLCVBar]) -> dict[str, FeatureScalar]:
        closes = [bar.close for bar in bars]
        macd, signal, histogram = cls.macd(closes, 12, 26, 9)
        return {
            "momentum.macd": macd,
            "momentum.macd_histogram": histogram,
            "momentum.macd_signal": signal,
            "momentum.roc_12": cls.roc(closes, 12),
            "momentum.rsi_14": cls.rsi(closes, 14),
        }
