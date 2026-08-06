"""Volume features extracted from the Phase 6 Technical Analyst."""

from itertools import pairwise

from app.domain.models.features import FeatureScalar
from app.domain.models.market_data import OHLCVBar


class VolumeFeatureGenerator:
    version = "1.0.0"
    minimum_bars = 20

    @staticmethod
    def obv(bars: list[OHLCVBar]) -> float:
        if not bars:
            raise ValueError("bars required")
        total = 0
        for previous, current in pairwise(bars):
            total += current.volume if current.close > previous.close else -current.volume if current.close < previous.close else 0
        return float(total)

    @staticmethod
    def rolling_volume_average(bars: list[OHLCVBar], period: int) -> float:
        if period <= 0 or len(bars) < period:
            raise ValueError("insufficient volume values")
        return sum(bar.volume for bar in bars[-period:]) / period

    @classmethod
    def relative_volume(cls, bars: list[OHLCVBar], period: int) -> float:
        average = cls.rolling_volume_average(bars, period)
        return bars[-1].volume / average if average else 0.0

    @staticmethod
    def vwap(bars: list[OHLCVBar]) -> float:
        volume = sum(bar.volume for bar in bars)
        if not bars or not volume:
            raise ValueError("positive aggregate volume required")
        return sum(((bar.high + bar.low + bar.close) / 3) * bar.volume for bar in bars) / volume

    @classmethod
    def generate(cls, bars: list[OHLCVBar]) -> dict[str, FeatureScalar]:
        return {
            "volume.average_20": cls.rolling_volume_average(bars, 20),
            "volume.obv": cls.obv(bars),
            "volume.relative_20": cls.relative_volume(bars, 20),
            "volume.vwap": cls.vwap(bars),
        }
