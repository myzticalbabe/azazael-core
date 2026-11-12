"""
Eve Unleashed — Inference Cache
================================
LRU cache with TTL-based expiration for inference results.

Caches results keyed by (handle, input_hash, framework) tuple.
Evicts least-recently-used entries when size exceeds cache_size_mb.
Expires entries older than cache_ttl_seconds.
"""

import hashlib
import json
import sys
import threading
import time
from typing import Any, List

import numpy as np

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def _hash_inputs(inputs: Any) -> str:
    """Deterministic hash of inference inputs for cache keying."""
    if isinstance(inputs, np.ndarray):
        return hashlib.sha256(inputs.tobytes()).hexdigest()[:16]
    if isinstance(inputs, (list, tuple)):
        try:
            arr = np.array(inputs, dtype=np.float32)
            return hashlib.sha256(arr.tobytes()).hexdigest()[:16]
        except (ValueError, TypeError) as exc:
            note_failure("cache:L29", exc)
    serialized = json.dumps(inputs, sort_keys=True, default=str).encode()
    return hashlib.sha256(serialized).hexdigest()[:16]


def _estimate_size(result: Any) -> int:
    """Estimate memory size of a result in bytes."""
    if isinstance(result, np.ndarray):
        return result.nbytes
    try:
        return sys.getsizeof(result)
    except TypeError as exc:
        note_failure("cache:L41", exc)
        return 1024  # conservative guess for complex objects


class InferenceCache:
    """Thread-safe LRU + TTL cache for inference results.

    Integrated via engine.infer() — when enabled, identical inputs on the
    same model return cached results without re-running inference.
    """

    def __init__(
        self,
        max_size_bytes: int = 512 * 1024 * 1024,  # default 512 MB
        ttl_seconds: int = 300,                     # default 5 minutes
    ):
        self._max_size = max_size_bytes
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._store: dict[tuple[str, str, str], tuple[float, Any]] = {}
        self._hits: int = 0
        self._misses: int = 0
        self._current_size: int = 0

    @property
    def stats(self) -> dict[str, Any]:
        """Cache usage statistics."""
        total = self._hits + self._misses
        return {
            "entries": len(self._store),
            "size_bytes": self._current_size,
            "max_size_bytes": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total > 0 else 0.0,
        }

    def get(self, handle: str, inputs: Any, framework: str) -> (Any | None):
        """Look up a cached result. Returns None on miss or expiry."""
        key = self._make_key(handle, inputs, framework)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            timestamp, result = entry
            if time.time() - timestamp > self._ttl:
                # Expired — remove and count as miss
                self._current_size -= _estimate_size(result)
                del self._store[key]
                self._misses += 1
                return None
            # Cache hit — move to end (LRU promotion)
            del self._store[key]
            self._store[key] = entry
            self._hits += 1
            return result

    def put(self, handle: str, inputs: Any, framework: str, result: Any):
        """Store a result in the cache, evicting LRU entries if needed."""
        key = self._make_key(handle, inputs, framework)
        entry_size = _estimate_size(result)
        with self._lock:
            # If key already exists, remove old entry first
            if key in self._store:
                _, old_result = self._store.pop(key)
                self._current_size -= _estimate_size(old_result)

            # Evict LRU entries until we have room
            while self._store and self._current_size + entry_size > self._max_size:
                oldest_key = next(iter(self._store))
                _, old_result = self._store.pop(oldest_key)
                self._current_size -= _estimate_size(old_result)

            self._store[key] = (time.time(), result)
            self._current_size += entry_size

    def invalidate(self, handle: (str | None) = None):
        """Clear entries for a specific handle, or the entire cache."""
        with self._lock:
            if handle is None:
                self._store.clear()
                self._current_size = 0
            else:
                to_remove = [k for k in self._store if k[0] == handle]
                for k in to_remove:
                    _, result = self._store.pop(k)
                    self._current_size -= _estimate_size(result)

    def clear(self):
        """Clear all cache entries."""
        self.invalidate()

    @staticmethod
    def _make_key(handle: str, inputs: Any, framework: str) -> tuple[str, str, str]:
        return (handle, _hash_inputs(inputs), framework)
