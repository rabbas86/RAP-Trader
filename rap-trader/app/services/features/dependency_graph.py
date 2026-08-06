"""Validated deterministic feature dependency graph."""

from __future__ import annotations

from collections.abc import Iterable

from app.domain.models.features import FeatureError, FeatureErrorCode, FeatureId


class FeatureDependencyGraph:
    def __init__(self) -> None:
        self._edges: dict[str, tuple[str, ...]] = {}

    def add(self, feature_id: FeatureId | str, dependencies: Iterable[FeatureId | str] = ()) -> None:
        key = str(feature_id)
        values = tuple(sorted({str(item) for item in dependencies}))
        if key in values:
            raise FeatureError(FeatureErrorCode.DEPENDENCY_CYCLE, "A feature cannot depend on itself")
        previous = self._edges.get(key)
        self._edges[key] = values
        try:
            self._validate_cycles()
        except FeatureError:
            if previous is None:
                del self._edges[key]
            else:
                self._edges[key] = previous
            raise

    def remove(self, feature_id: FeatureId | str) -> None:
        key = str(feature_id)
        dependants = sorted(name for name, dependencies in self._edges.items() if key in dependencies)
        if dependants:
            raise FeatureError(FeatureErrorCode.INVALID_DEPENDENCY, f"Feature is required by: {', '.join(dependants)}")
        self._edges.pop(key, None)

    def dependencies(self, feature_id: FeatureId | str, *, transitive: bool = False) -> tuple[str, ...]:
        key = str(feature_id)
        if key not in self._edges:
            raise FeatureError(FeatureErrorCode.UNKNOWN_FEATURE, "Feature is not registered")
        if not transitive:
            return self._edges[key]
        result: set[str] = set()

        def visit(node: str) -> None:
            for dependency in self._edges.get(node, ()):
                if dependency not in result:
                    result.add(dependency)
                    visit(dependency)

        visit(key)
        return tuple(sorted(result))

    def validate_references(self) -> None:
        missing = sorted({dependency for values in self._edges.values() for dependency in values if dependency not in self._edges})
        if missing:
            raise FeatureError(FeatureErrorCode.INVALID_DEPENDENCY, f"Unknown feature dependencies: {', '.join(missing)}")
        self._validate_cycles()

    def topological_order(self, selected: Iterable[FeatureId | str] | None = None) -> tuple[str, ...]:
        self.validate_references()
        targets = set(self._edges if selected is None else (str(item) for item in selected))
        unknown = targets - self._edges.keys()
        if unknown:
            raise FeatureError(FeatureErrorCode.UNKNOWN_FEATURE, f"Unknown features: {', '.join(sorted(unknown))}")
        required = set(targets)
        for target in tuple(targets):
            required.update(self.dependencies(target, transitive=True))
        order: list[str] = []
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visited:
                return
            for dependency in self._edges[node]:
                visit(dependency)
            visited.add(node)
            if node in required:
                order.append(node)

        for target in sorted(targets):
            visit(target)
        return tuple(order)

    def snapshot(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return tuple((key, self._edges[key]) for key in sorted(self._edges))

    def _validate_cycles(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise FeatureError(FeatureErrorCode.DEPENDENCY_CYCLE, "Feature dependency cycle detected")
            if node in visited:
                return
            visiting.add(node)
            for dependency in self._edges.get(node, ()):
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for node in self._edges:
            visit(node)
