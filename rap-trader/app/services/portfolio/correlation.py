"""Point-in-time pairwise return correlations."""

from __future__ import annotations

import math
from datetime import datetime
from itertools import pairwise

from app.domain.models.market_data import HistoricalBarsResult


class PortfolioCorrelationService:
    def __init__(self, minimum_samples: int = 3) -> None:
        self.minimum_samples = minimum_samples

    def correlations(self, history: list[HistoricalBarsResult], as_of: datetime) -> dict[tuple[str, str], float | None]:
        returns: dict[str, dict[datetime, float]] = {}
        for item in history:
            bars = [bar for bar in item.bars if bar.timestamp <= as_of]
            values: dict[datetime, float] = {}
            for previous, current in pairwise(bars):
                values[current.timestamp] = current.close / previous.close - 1.0
            returns[str(item.symbol)] = values
        symbols = sorted(returns)
        result: dict[tuple[str, str], float | None] = {}
        for index, left in enumerate(symbols):
            for right in symbols[index:]:
                common = sorted(returns[left].keys() & returns[right].keys())
                if len(common) < self.minimum_samples:
                    result[(left, right)] = None
                    continue
                xs, ys = [returns[left][time] for time in common], [returns[right][time] for time in common]
                result[(left, right)] = self._pearson(xs, ys)
        return result

    @staticmethod
    def _pearson(xs: list[float], ys: list[float]) -> float | None:
        mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
        numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
        denominator = math.sqrt(sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys))
        return None if denominator == 0 else max(-1.0, min(1.0, numerator / denominator))
