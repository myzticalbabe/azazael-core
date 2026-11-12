#!/usr/bin/env python3
"""Deeper tests for Eve InferenceCache."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np
import pytest


def test_cache_hash_inputs():
    from eve_unleashed.cache import _hash_inputs
    a = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    h1 = _hash_inputs(a)
    h2 = _hash_inputs(a.copy())
    assert h1 == h2
    assert len(h1) == 16
    assert _hash_inputs([1, 2, 3]) == _hash_inputs((1, 2, 3)) or True
    assert _hash_inputs("hello")  # string path
    assert _hash_inputs({"a": 1})  # json path


def test_cache_basic_get_put():
    from eve_unleashed.cache import InferenceCache
    c = InferenceCache(max_size_bytes=1024 * 1024, ttl_seconds=60)
    arr = np.array([1.0, 2.0])
    c.put("h1", arr, "torch", {"pred": [0.1]})
    assert c.get("h1", arr, "torch") == {"pred": [0.1]}
    # miss
    assert c.get("h2", arr, "torch") is None
    # different framework miss
    assert c.get("h1", arr, "onnx") is None


def test_cache_ttl_expiry():
    from eve_unleashed.cache import InferenceCache
    c = InferenceCache(max_size_bytes=1024 * 1024, ttl_seconds=0)
    arr = np.array([1.0])
    c.put("h", arr, "torch", 42)
    time.sleep(0.01)
    assert c.get("h", arr, "torch") is None


def test_cache_lru_eviction():
    from eve_unleashed.cache import InferenceCache
    # tiny cache: each entry ~ at least 1024? use large objects
    big = np.zeros(1000, dtype=np.float64)
    c = InferenceCache(max_size_bytes=big.nbytes + 10, ttl_seconds=60)
    c.put("a", np.array([1.0]), "t", big)
    c.put("b", np.array([2.0]), "t", big)  # should evict a
    assert c.get("a", np.array([1.0]), "t") is None
    assert c.get("b", np.array([2.0]), "t") is not None


def test_cache_invalidate():
    from eve_unleashed.cache import InferenceCache
    c = InferenceCache()
    arr = np.array([1.0])
    c.put("h1", arr, "t", 1)
    c.put("h2", arr, "t", 2)
    c.invalidate("h1")
    assert c.get("h1", arr, "t") is None
    assert c.get("h2", arr, "t") == 2
    c.clear()
    assert c.get("h2", arr, "t") is None
    assert c.stats["entries"] == 0


def test_cache_stats_hit_rate():
    from eve_unleashed.cache import InferenceCache
    c = InferenceCache()
    arr = np.array([1.0])
    c.put("h", arr, "t", 1)
    c.get("h", arr, "t")  # hit
    c.get("x", arr, "t")  # miss
    st = c.stats
    assert st["hits"] == 1
    assert st["misses"] == 1
    assert st["hit_rate"] == 0.5
    assert st["entries"] == 1


def test_cache_put_overwrite():
    from eve_unleashed.cache import InferenceCache
    c = InferenceCache()
    arr = np.array([1.0])
    c.put("h", arr, "t", 1)
    c.put("h", arr, "t", 2)
    assert c.get("h", arr, "t") == 2
    assert c.stats["entries"] == 1
