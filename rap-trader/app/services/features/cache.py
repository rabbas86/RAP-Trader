"""Bounded thread-safe immutable snapshot cache."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from threading import RLock

from app.domain.models.features import FeatureSnapshot
from app.domain.models.market_data import AdjustmentPolicy, SessionPolicy, Timeframe, _require_aware_utc


def configuration_hash(configuration: tuple[tuple[str, str], ...]) -> str:
    material = json.dumps(sorted(configuration), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(material.encode()).hexdigest()


@dataclass(frozen=True)
class FeatureCacheKey:
    ticker: str
    timeframe: Timeframe
    provider: str
    adjustment: AdjustmentPolicy
    session: SessionPolicy
    as_of: datetime
    configuration_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", _require_aware_utc(self.as_of))


class FeatureSnapshotCache:
    def __init__(self, max_size: int = 256) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        self.max_size = max_size
        self._items: OrderedDict[FeatureCacheKey, FeatureSnapshot] = OrderedDict()
        self._lock = RLock()
        self.hits = 0
        self.misses = 0

    def get(self, key: FeatureCacheKey) -> FeatureSnapshot | None:
        with self._lock:
            value = self._items.get(key)
            if value is None:
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: FeatureCacheKey, snapshot: FeatureSnapshot) -> None:
        with self._lock:
            self._items[key] = snapshot
            self._items.move_to_end(key)
            while len(self._items) > self.max_size:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
