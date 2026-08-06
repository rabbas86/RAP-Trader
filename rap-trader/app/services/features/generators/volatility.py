"""Volatility features extracted from the Phase 6 Technical Analyst."""

from math import sqrt

from app.domain.models.features import FeatureScalar
from app.domain.models.market_data import OHLCVBar
from app.services.features.generators.trend import TrendFeatureGenerator


class VolatilityFeatureGenerator:
    version = "1.0.0"
    minimum_bars = 20

    @staticmethod
    def true_ranges(bars: list[OHLCVBar]) -> list[float]:
        if not bars:
            raise ValueError("bars required")
        return [
            bar.high - bar.low
            if index == 0
            else max(bar.high - bar.low, abs(bar.high - bars[index - 1].close), abs(bar.low - bars[index - 1].close))
            for index, bar in enumerate(bars)
        ]

    @classmethod
    def atr(cls, bars: list[OHLCVBar], period: int) -> float:
        ranges = cls.true_ranges(bars)
        if period <= 0 or len(ranges) < period:
            raise ValueError("insufficient values for ATR")
        value = sum(ranges[:period]) / period
        for item in ranges[period:]:
            value = (value * (period - 1) + item) / period
        return value

    @staticmethod
    def bollinger_bands(values: list[float], period: int = 20, deviations: float = 2) -> tuple[float, float, float]:
        middle = TrendFeatureGenerator.sma(values, period)
        window = values[-period:]
        std = sqrt(sum((value - middle) ** 2 for value in window) / period)
        return middle - deviations * std, middle, middle + deviations * std

    @staticmethod
    def bollinger_bandwidth(lower: float, middle: float, upper: float) -> float:
        return (upper - lower) / middle if middle else 0.0

    @classmethod
    def generate(cls, bars: list[OHLCVBar]) -> dict[str, FeatureScalar]:
        lower, middle, upper = cls.bollinger_bands([bar.close for bar in bars])
        ranges = cls.true_ranges(bars)
        return {
            "volatility.atr_14": cls.atr(bars, 14),
            "volatility.bollinger_bandwidth": cls.bollinger_bandwidth(lower, middle, upper),
            "volatility.bollinger_lower": lower,
            "volatility.bollinger_middle": middle,
            "volatility.bollinger_upper": upper,
            "volatility.true_range": ranges[-1],
        }
