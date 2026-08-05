from abc import ABC, abstractmethod
from collections import OrderedDict
from threading import RLock
from time import monotonic
from typing import TypeVar

T = TypeVar("T")


class AbstractCache[T](ABC):
    @abstractmethod
    def get(self, key: str) -> T | None:
        """Return a cached value, or None when absent or expired."""

    @abstractmethod
    def set(self, key: str, value: T) -> None:
        """Store a value under key."""

    @abstractmethod
    def clear(self) -> None:
        """Remove every cached value."""


class InMemoryCache[T](AbstractCache[T]):
    def __init__(self, ttl_seconds: float = 300.0, max_size: int = 128) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._values: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._lock = RLock()

    def get(self, key: str) -> T | None:
        with self._lock:
            item = self._values.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= monotonic():
                del self._values[key]
                return None
            self._values.move_to_end(key)
            return value

    def set(self, key: str, value: T) -> None:
        with self._lock:
            self._values[key] = (monotonic() + self.ttl_seconds, value)
            self._values.move_to_end(key)
            while len(self._values) > self.max_size:
                self._values.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
