"""Bounded in-process caches (LRU) for games, evals, and opening lookups."""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock
from typing import Generic, Hashable, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class BoundedLRU(Generic[K, V]):
    """Thread-safe LRU dict with a fixed max size."""

    def __init__(self, maxsize: int = 256) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self._maxsize = maxsize
        self._data: OrderedDict[K, V] = OrderedDict()
        self._lock = Lock()

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._data

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def get(self, key: K, default: V | None = None) -> V | None:
        with self._lock:
            if key not in self._data:
                return default
            self._data.move_to_end(key)
            return self._data[key]

    def __getitem__(self, key: K) -> V:
        with self._lock:
            self._data.move_to_end(key)
            return self._data[key]

    def __setitem__(self, key: K, value: V) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
