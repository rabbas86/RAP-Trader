"""Observed price features."""

from app.domain.models.features import FeatureScalar
from app.domain.models.market_data import OHLCVBar


class PriceFeatureGenerator:
    version = "1.0.0"
    minimum_bars = 2

    @staticmethod
    def generate(bars: list[OHLCVBar]) -> dict[str, FeatureScalar]:
        if len(bars) < 2:
            raise ValueError("at least two bars are required")
        latest, previous = bars[-1], bars[-2]
        return {
            "price.close": latest.close,
            "price.high": latest.high,
            "price.low": latest.low,
            "price.open": latest.open,
            "price.return_1": latest.close / previous.close - 1,
            "price.typical": (latest.high + latest.low + latest.close) / 3,
        }
