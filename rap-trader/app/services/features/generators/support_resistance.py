"""Causal support/resistance clustering extracted from Phase 6."""

from typing import Literal

from app.domain.models.analyst import EvidenceStrength
from app.domain.models.features import FeatureScalar
from app.domain.models.market_data import OHLCVBar
from app.domain.models.technical import SwingPoint, TechnicalLevel
from app.services.features.generators.structure import confirmed_swings


def clustered_levels(swings: list[SwingPoint], current_price: float, *, tolerance: float = 0.01, limit: int = 6) -> list[TechnicalLevel]:
    if tolerance <= 0 or limit <= 0 or current_price <= 0:
        raise ValueError("price, tolerance, and limit must be positive")
    clusters: list[list[SwingPoint]] = []
    for point in sorted(swings, key=lambda item: item.price):
        cluster = next(
            (
                items
                for items in clusters
                if abs(point.price - sum(item.price for item in items) / len(items)) / (sum(item.price for item in items) / len(items))
                <= tolerance
            ),
            None,
        )
        if cluster is None:
            clusters.append([point])
        else:
            cluster.append(point)
    levels: list[TechnicalLevel] = []
    for cluster in clusters:
        price = sum(point.price for point in cluster) / len(cluster)
        kind: Literal["support", "resistance"] = "support" if current_price >= price else "resistance"
        broken = any(
            (point.type == "low" and current_price < price * (1 - tolerance))
            or (point.type == "high" and current_price > price * (1 + tolerance))
            for point in cluster
        )
        count = len(cluster)
        strength = EvidenceStrength.STRONG if count >= 3 else EvidenceStrength.MODERATE if count == 2 else EvidenceStrength.WEAK
        levels.append(
            TechnicalLevel(
                price=price,
                level_type=kind,
                strength=strength,
                confirmed_at=max(point.confirmed_at for point in cluster),
                touch_count=count,
                broken=broken,
            )
        )
    levels.sort(key=lambda item: (-item.touch_count, abs(item.price - current_price), item.price))
    return levels[:limit]


class SupportResistanceFeatureGenerator:
    version = "1.0.0"
    minimum_bars = 5

    @staticmethod
    def generate(bars: list[OHLCVBar]) -> dict[str, FeatureScalar]:
        levels = clustered_levels(confirmed_swings(bars), bars[-1].close)
        supports = [level for level in levels if level.level_type == "support" and not level.broken]
        resistances = [level for level in levels if level.level_type == "resistance" and not level.broken]
        return {
            "support_resistance.broken_count": sum(level.broken for level in levels),
            "support_resistance.level_count": len(levels),
            "support_resistance.nearest_resistance": min((level.price for level in resistances), default=None),
            "support_resistance.nearest_support": max((level.price for level in supports), default=None),
            "support_resistance.touch_count": sum(level.touch_count for level in levels),
        }
