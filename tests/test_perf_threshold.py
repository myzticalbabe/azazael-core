"""Performance threshold tests — fail if write/query exceeds time budget."""

import sys
import json
import tempfile
import os
import time
from pathlib import Path

sys.path.insert(0, "src")

MAX_WRITE_MS = 50  # 50ms per write max
MAX_QUERY_MS = 100  # 100ms per query max
MAX_IMPORT_MS = 500  # 500ms for batch import


def _setup():
    old_env = os.environ.get("AZAZAEL_MEMORY_DIR", "")
    tmpdir = Path(tempfile.mkdtemp())
    os.environ["AZAZAEL_MEMORY_DIR"] = str(tmpdir)
    from CORE import direct_memory
    direct_memory.DB_PATH = direct_memory._get_db_path()
    return tmpdir, old_env


def _teardown(tmpdir, old_env):
    if old_env:
        os.environ["AZAZAEL_MEMORY_DIR"] = old_env
    else:
        os.environ.pop("AZAZAEL_MEMORY_DIR", None)
    import shutil
    try:
        shutil.rmtree(str(tmpdir))
    except Exception:
        pass


def test_single_write_threshold():
    """Single event write should complete under 50ms."""
    tmpdir, old_env = _setup()
    try:
        from CORE.direct_memory import DirectMemory

        dm = DirectMemory()
        start = time.perf_counter()
        dm.record("perf_test", {"key": "value"}, tags=["perf"])
        elapsed_ms = (time.perf_counter() - start) * 1000
        dm.end()

        assert elapsed_ms <= MAX_WRITE_MS, \
            f"Write took {elapsed_ms:.1f}ms, threshold {MAX_WRITE_MS}ms"
    finally:
        _teardown(tmpdir, old_env)


def test_batch_import_threshold():
    """Batch import of 100 events should complete under 500ms."""
    tmpdir, old_env = _setup()
    try:
        from CORE.direct_memory import DirectMemory

        events = [
            {"event_type": "bulk_test", "data": {"index": i},
             "severity": "info", "tags": ["bulk"]}
            for i in range(100)
        ]

        start = time.perf_counter()
        count = DirectMemory.import_events(events, session_id="perf_bulk")
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert count == 100, f"Expected 100, got {count}"
        assert elapsed_ms <= MAX_IMPORT_MS, \
            f"Import took {elapsed_ms:.1f}ms, threshold {MAX_IMPORT_MS}ms"
    finally:
        _teardown(tmpdir, old_env)


def test_query_threshold():
    """Summary stats query should complete under 100ms."""
    tmpdir, old_env = _setup()
    try:
        from CORE.direct_memory import DirectMemory

        # Seed data
        dm = DirectMemory(session_id="perf_seed")
        for i in range(50):
            dm.record("perf_seed", {"i": i})
        dm.end()

        start = time.perf_counter()
        stats = DirectMemory.summary_stats()
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert isinstance(stats, dict)
        assert elapsed_ms <= MAX_QUERY_MS, \
            f"Query took {elapsed_ms:.1f}ms, threshold {MAX_QUERY_MS}ms"
    finally:
        _teardown(tmpdir, old_env)


def test_filtered_query_threshold():
    """Filtered query should complete under 100ms."""
    tmpdir, old_env = _setup()
    try:
        from CORE.direct_memory import DirectMemory

        dm = DirectMemory(session_id="perf_filter")
        for i in range(50):
            dm.record("perf_filter", {"i": i})
        dm.end()

        start = time.perf_counter()
        results = DirectMemory.filtered_query(event_type="perf_filter", limit=10)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert isinstance(results, list)
        assert elapsed_ms <= MAX_QUERY_MS, \
            f"Filtered query took {elapsed_ms:.1f}ms, threshold {MAX_QUERY_MS}ms"
    finally:
        _teardown(tmpdir, old_env)
