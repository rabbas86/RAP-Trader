"""Feature snapshot storage facade and statistics."""

from __future__ import annotations

from app.domain.models.features import FeatureSnapshot, FeatureStoreStatistics
from app.services.features.cache import FeatureCacheKey, FeatureSnapshotCache


class FeatureStore:
    def __init__(self, cache: FeatureSnapshotCache | None = None) -> None:
        self.cache = cache or FeatureSnapshotCache()
        self.computations = 0

    def get(self, key: FeatureCacheKey) -> FeatureSnapshot | None:
        return self.cache.get(key)

    def put(self, key: FeatureCacheKey, snapshot: FeatureSnapshot) -> None:
        self.cache.set(key, snapshot)
        self.computations += 1

    def statistics(self, registered_features: int) -> FeatureStoreStatistics:
        return FeatureStoreStatistics(
            registered_features=registered_features,
            cached_snapshots=len(self.cache),
            cache_hits=self.cache.hits,
            cache_misses=self.cache.misses,
            computations=self.computations,
        )
