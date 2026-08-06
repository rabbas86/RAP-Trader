"""Thread-safe feature registry and dependency-aware computation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Any

from app.domain.models.features import FeatureError, FeatureErrorCode, FeatureId, FeatureMetadata, FeatureScalar
from app.domain.models.market_data import OHLCVBar
from app.services.features.dependency_graph import FeatureDependencyGraph
from app.services.features.validation import validate_scalar


@dataclass
class FeatureComputationContext:
    bars: list[OHLCVBar]
    as_of: datetime
    extras: dict[str, Any] = field(default_factory=dict)
    batches: dict[str, dict[str, FeatureScalar]] = field(default_factory=dict)


FeatureCompute = Callable[[FeatureComputationContext, dict[str, FeatureScalar]], FeatureScalar]


@dataclass(frozen=True)
class RegisteredFeature:
    metadata: FeatureMetadata
    compute: FeatureCompute


class FeatureRegistry:
    def __init__(self) -> None:
        self._features: dict[str, RegisteredFeature] = {}
        self._graph = FeatureDependencyGraph()
        self._lock = RLock()

    def register(self, metadata: FeatureMetadata, compute: FeatureCompute) -> None:
        key = str(metadata.feature_id)
        with self._lock:
            if key in self._features:
                raise FeatureError(FeatureErrorCode.DUPLICATE_FEATURE, "Feature is already registered")
            self._graph.add(key, (dependency.feature_id for dependency in metadata.dependencies))
            self._features[key] = RegisteredFeature(metadata, compute)

    def unregister(self, feature_id: FeatureId | str) -> None:
        key = str(feature_id)
        with self._lock:
            if key not in self._features:
                raise FeatureError(FeatureErrorCode.UNKNOWN_FEATURE, "Feature is not registered")
            self._graph.remove(key)
            del self._features[key]

    def compute(
        self, feature_id: FeatureId | str, context: FeatureComputationContext, computed: dict[str, FeatureScalar] | None = None
    ) -> FeatureScalar:
        key = str(feature_id)
        values = {} if computed is None else computed
        with self._lock:
            if key not in self._features:
                raise FeatureError(FeatureErrorCode.UNKNOWN_FEATURE, "Feature is not registered")
            order = self._graph.topological_order((key,))
            definitions = {name: self._features[name] for name in order}
        for name in order:
            if name in values:
                continue
            try:
                values[name] = validate_scalar(definitions[name].compute(context, values))
            except FeatureError:
                raise
            except (ArithmeticError, KeyError, TypeError, ValueError) as exc:
                raise FeatureError(FeatureErrorCode.COMPUTATION_FAILED, f"Feature computation failed: {name}", str(exc)) from exc
        return values[key]

    def compute_many(self, context: FeatureComputationContext, selected: tuple[FeatureId, ...] | None = None) -> dict[str, FeatureScalar]:
        with self._lock:
            order = self._graph.topological_order(selected)
        values: dict[str, FeatureScalar] = {}
        for name in order:
            self.compute(name, context, values)
        return {name: values[name] for name in order if selected is None or name in {str(item) for item in selected}}

    def list(self) -> tuple[FeatureMetadata, ...]:
        with self._lock:
            return tuple(self._features[key].metadata for key in sorted(self._features))

    def dependencies(self, feature_id: FeatureId | str, *, transitive: bool = False) -> tuple[str, ...]:
        with self._lock:
            return self._graph.dependencies(feature_id, transitive=transitive)

    def validate(self) -> None:
        with self._lock:
            self._graph.validate_references()

    def dependency_snapshot(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        with self._lock:
            return self._graph.snapshot()
