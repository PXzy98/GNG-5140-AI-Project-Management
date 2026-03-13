"""
MockStorage — in-memory dict-based storage for prototype/testing.

All services should depend on get_storage() factory so tests can
inject a fresh instance without side effects between runs.
"""

from __future__ import annotations

import threading
from typing import Any


class MockStorage:
    """Thread-safe in-memory key-value store with namespaced collections."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

    def put(self, collection: str, key: str, value: Any) -> None:
        with self._lock:
            if collection not in self._data:
                self._data[collection] = {}
            self._data[collection][key] = value

    def get(self, collection: str, key: str) -> Any | None:
        with self._lock:
            return self._data.get(collection, {}).get(key)

    def list(self, collection: str, filters: dict[str, Any] | None = None) -> list[Any]:
        with self._lock:
            items = list(self._data.get(collection, {}).values())
        if not filters:
            return items
        result = []
        for item in items:
            if isinstance(item, dict):
                if all(item.get(k) == v for k, v in filters.items()):
                    result.append(item)
            else:
                # Pydantic models / dataclasses — try attribute access
                if all(getattr(item, k, None) == v for k, v in filters.items()):
                    result.append(item)
        return result

    def delete(self, collection: str, key: str) -> bool:
        with self._lock:
            if collection in self._data and key in self._data[collection]:
                del self._data[collection][key]
                return True
            return False

    def exists(self, collection: str, key: str) -> bool:
        with self._lock:
            return key in self._data.get(collection, {})

    def count(self, collection: str) -> int:
        with self._lock:
            return len(self._data.get(collection, {}))

    def clear(self, collection: str | None = None) -> None:
        with self._lock:
            if collection:
                self._data.pop(collection, None)
            else:
                self._data.clear()

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def find_by_field(self, collection: str, field: str, value: Any) -> list[Any]:
        return self.list(collection, filters={field: value})

    def upsert(self, collection: str, key: str, value: Any) -> None:
        self.put(collection, key, value)


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_default_storage: MockStorage | None = None
_storage_lock = threading.Lock()


def get_storage() -> MockStorage:
    """Return the process-level default MockStorage (singleton)."""
    global _default_storage
    with _storage_lock:
        if _default_storage is None:
            _default_storage = MockStorage()
        return _default_storage


def reset_storage() -> None:
    """Reset the singleton (useful in tests)."""
    global _default_storage
    with _storage_lock:
        _default_storage = None
