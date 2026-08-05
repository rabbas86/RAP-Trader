import time

from app.services.market_data import InMemoryCache


def test_cache_hit_miss_and_clear() -> None:
    cache = InMemoryCache[str](ttl_seconds=10)
    assert cache.get("key") is None
    cache.set("key", "value")
    assert cache.get("key") == "value"
    cache.clear()
    assert cache.get("key") is None


def test_cache_entry_expires() -> None:
    cache = InMemoryCache[str](ttl_seconds=0.01)
    cache.set("key", "value")
    time.sleep(0.02)
    assert cache.get("key") is None


def test_cache_evicts_least_recently_used_entry() -> None:
    cache = InMemoryCache[str](ttl_seconds=10, max_size=2)
    cache.set("first", "1")
    cache.set("second", "2")
    assert cache.get("first") == "1"
    cache.set("third", "3")
    assert cache.get("second") is None
