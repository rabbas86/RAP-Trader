"""Feature adapter for previously-computed backtest metrics."""

from math import isfinite
from typing import Any

from app.domain.models.features import FeatureScalar


class BacktestFeatureGenerator:
    version = "1.0.0"

    @staticmethod
    def generate(metrics: dict[str, Any] | None) -> dict[str, FeatureScalar]:
        if metrics is None:
            return {}
        result: dict[str, FeatureScalar] = {}
        for name, value in sorted(metrics.items()):
            if isinstance(value, bool | str | int) or value is None or isinstance(value, float) and isfinite(value):
                result[f"backtest.{name}"] = value
        return result
