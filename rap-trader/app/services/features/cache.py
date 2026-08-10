"""Bounded thread-safe immutable snapshot cache."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Any

from app.domain.models.features import FeatureSnapshot
from app.domain.models.market_data import AdjustmentPolicy, SessionPolicy, Timeframe, _require_aware_utc


def configuration_hash(configuration: tuple[tuple[str, str], ...]) -> str:
    material = json.dumps(sorted(configuration), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(material.encode()).hexdigest()


def _fingerprint(obj: object) -> str:
    """Return a stable SHA-256 fingerprint of a serializable object.

    Only a hash of the serialized payload is embedded in the key — never the raw payload.
    """
    if obj is None:
        return "none"
    material = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)
    return hashlib.sha256(material.encode()).hexdigest()


def kronos_fingerprint(forecast: Any | None) -> str:
    """Fingerprint a KronosForecast for cache isolation (never stores raw payload)."""
    if forecast is None:
        return "none"
    if hasattr(forecast, "model_dump"):
        forecast = forecast.model_dump(mode="json")
    return _fingerprint(forecast)


def backtest_fingerprint(metrics: Any | None) -> str:
    """Fingerprint backtest metrics for cache isolation (never stores raw payload)."""
    if metrics is None:
        return "none"
    if hasattr(metrics, "model_dump"):
        metrics = metrics.model_dump(mode="json")
    return _fingerprint(metrics)


def build_cache_key(
    *,
    ticker: str,
    timeframe: Timeframe,
    provider: str,
    adjustment: AdjustmentPolicy,
    session: SessionPolicy,
    as_of: datetime,
    lookback: int,
    selected_feature_ids: tuple[str, ...] | None,
    configuration_hash_value: str,
    schema_version: str,
    kronos_fp: str,
    backtest_fp: str,
) -> FeatureCacheKey:
    """Centralized, deterministic cache-key builder.

    Every dimension that affects the computation result is encoded so that
    requests with different lookbacks, Kronos inputs, or backtest inputs never
    collide.  Only the SHA-256 *fingerprint* of variable-length payloads is
    stored in the key — never the raw payload itself.
    """
    return FeatureCacheKey(
        ticker=ticker,
        timeframe=timeframe,
        provider=provider,
        adjustment=adjustment,
        session=session,
        as_of=as_of,
        lookback=lookback,
        configuration_hash=configuration_hash_value,
        schema_version=schema_version,
        kronos_fingerprint=kronos_fp,
        backtest_fingerprint=backtest_fp,
    )


@dataclass(frozen=True)
class FeatureCacheKey:
    ticker: str
    timeframe: Timeframe
    provider: str
    adjustment: AdjustmentPolicy
    session: SessionPolicy
    as_of: datetime
    lookback: int
    configuration_hash: str
    schema_version: str
    kronos_fingerprint: str
    backtest_fingerprint: str

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
