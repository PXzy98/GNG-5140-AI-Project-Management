"""
Redis client stub with in-memory fallback.

For prototype/testing, all operations use an in-memory dict.
Set USE_REDIS=true in .env to switch to a real Redis connection.
"""

from __future__ import annotations

import time
from typing import Any


class InMemoryCache:
    """TTL-aware in-memory cache used when Redis is unavailable."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float | None]] = {}

    def set(self, key: str, value: Any, ex: int | None = None) -> None:
        expires_at = time.time() + ex if ex else None
        self._store[key] = (value, expires_at)

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at and time.time() > expires_at:
            del self._store[key]
            return None
        return value

    def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0

    def exists(self, key: str) -> bool:
        return self.get(key) is not None

    def flush(self) -> None:
        self._store.clear()


_cache: InMemoryCache | None = None


def get_cache() -> InMemoryCache:
    global _cache
    if _cache is None:
        _cache = InMemoryCache()
    return _cache
