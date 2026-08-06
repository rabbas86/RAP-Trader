"""Feature adapter for an already-produced Kronos forecast.

The generator never invokes a model and never interprets a forecast as an action.
"""

from app.domain.models.features import FeatureScalar
from app.domain.models.kronos import KronosForecast


class KronosFeatureGenerator:
    version = "1.0.0"

    @staticmethod
    def generate(forecast: KronosForecast | None) -> dict[str, FeatureScalar]:
        if forecast is None or not forecast.bars:
            return {}
        first, last = forecast.bars[0], forecast.bars[-1]
        closes = [bar.close for bar in forecast.bars]
        return {
            "kronos.forecast_change": last.close / first.open - 1,
            "kronos.forecast_high": max(bar.high for bar in forecast.bars),
            "kronos.forecast_horizon": forecast.horizon,
            "kronos.forecast_low": min(bar.low for bar in forecast.bars),
            "kronos.forecast_mean_close": sum(closes) / len(closes),
        }
