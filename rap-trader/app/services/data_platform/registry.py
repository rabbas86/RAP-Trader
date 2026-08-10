"""Thread-safe data-source registry."""

from __future__ import annotations

from threading import RLock
from typing import Any

from app.domain.models.data_platform import DataSourceIdentity


class DataSourceRegistry:
    def __init__(self) -> None:
        self._sources: dict[str, tuple[DataSourceIdentity, Any]] = {}
        self._lock = RLock()

    @staticmethod
    def _key(source: DataSourceIdentity | str) -> str:
        return source if isinstance(source, str) else f"{source.provider}:{source.dataset}"

    def register(self, source: DataSourceIdentity, adapter: Any = None, *, replace: bool = False) -> None:
        key = self._key(source)
        with self._lock:
            if key in self._sources and not replace:
                raise ValueError(f"data source already registered: {key}")
            self._sources[key] = (source, adapter)

    def unregister(self, source: DataSourceIdentity | str) -> DataSourceIdentity:
        key = self._key(source)
        with self._lock:
            try:
                return self._sources.pop(key)[0]
            except KeyError as exc:
                raise KeyError(f"data source is not registered: {key}") from exc

    def get(self, key: str) -> Any:
        with self._lock:
            source, adapter = self._sources[key]
            return adapter if adapter is not None else source

    def metadata(self, key: str) -> DataSourceIdentity:
        with self._lock:
            return self._sources[key][0]

    def list(self) -> tuple[DataSourceIdentity, ...]:
        with self._lock:
            return tuple(self._sources[key][0] for key in sorted(self._sources))

    @property
    def sources(self) -> tuple[DataSourceIdentity, ...]:
        return self.list()


__all__ = ["DataSourceRegistry"]
