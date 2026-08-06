"""Support and resistance clustering from causally confirmed swings."""

from __future__ import annotations

from typing import Literal

from app.domain.models.analyst import EvidenceStrength
from app.domain.models.technical import SwingPoint, TechnicalLevel


def clustered_levels(swings: list[SwingPoint], current_price: float, *, tolerance: float = 0.01, limit: int = 6) -> list[TechnicalLevel]:
    if tolerance <= 0 or limit <= 0 or current_price <= 0:
        raise ValueError("price, tolerance, and limit must be positive")
    clusters: list[list[SwingPoint]] = []
    for point in sorted(swings, key=lambda item: item.price):
        cluster = next(
            (
                items
                for items in clusters
                if abs(point.price - sum(x.price for x in items) / len(items)) / (sum(x.price for x in items) / len(items)) <= tolerance
            ),
            None,
        )
        if cluster is None:
            clusters.append([point])
        else:
            cluster.append(point)
    levels: list[TechnicalLevel] = []
    latest = max((point.confirmed_at for point in swings), default=None)
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
                confirmed_at=max(point.confirmed_at for point in cluster) if latest else cluster[0].confirmed_at,
                touch_count=count,
                broken=broken,
            )
        )
    levels.sort(key=lambda item: (-item.touch_count, abs(item.price - current_price), item.price))
    return levels[:limit]
