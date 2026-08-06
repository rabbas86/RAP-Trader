"""Trend features extracted from the Phase 6 Technical Analyst."""

from app.domain.models.features import FeatureScalar
from app.domain.models.market_data import OHLCVBar


class TrendFeatureGenerator:
    version = "1.0.0"
    minimum_bars = 51

    @staticmethod
    def sma(values: list[float], period: int) -> float:
        if period <= 0 or len(values) < period:
            raise ValueError("insufficient values for SMA")
        return sum(values[-period:]) / period

    @staticmethod
    def ema_series(values: list[float], period: int) -> list[float]:
        if period <= 0 or len(values) < period:
            raise ValueError("insufficient values for EMA")
        alpha = 2 / (period + 1)
        result = [sum(values[:period]) / period]
        for value in values[period:]:
            result.append(value * alpha + result[-1] * (1 - alpha))
        return result

    @classmethod
    def ema(cls, values: list[float], period: int) -> float:
        return cls.ema_series(values, period)[-1]

    @classmethod
    def moving_average_slope(cls, values: list[float], period: int, *, exponential: bool = False) -> float:
        if len(values) < period + 1:
            raise ValueError("insufficient values for moving-average slope")
        current = cls.ema(values, period) if exponential else cls.sma(values, period)
        previous = cls.ema(values[:-1], period) if exponential else cls.sma(values[:-1], period)
        return (current - previous) / previous if previous else 0.0

    @classmethod
    def crossover(cls, values: list[float], fast: int, slow: int) -> tuple[str, int]:
        if fast >= slow or len(values) < slow:
            raise ValueError("insufficient values for crossover")
        states = [cls.sma(values[:end], fast) >= cls.sma(values[:end], slow) for end in range(slow, len(values) + 1)]
        age = 0
        for state in reversed(states[:-1]):
            if state != states[-1]:
                break
            age += 1
        return ("above" if states[-1] else "below", age)

    @classmethod
    def generate(cls, bars: list[OHLCVBar]) -> dict[str, FeatureScalar]:
        closes = [bar.close for bar in bars]
        state, age = cls.crossover(closes, 10, 50)
        return {
            "trend.crossover_age": age,
            "trend.crossover_state": state,
            "trend.ema_12": cls.ema(closes, 12),
            "trend.ema_26": cls.ema(closes, 26),
            "trend.ema_slope": cls.moving_average_slope(closes, 12, exponential=True),
            "trend.sma_10": cls.sma(closes, 10),
            "trend.sma_20": cls.sma(closes, 20),
            "trend.sma_50": cls.sma(closes, 50),
            "trend.sma_slope": cls.moving_average_slope(closes, 10),
        }
