"""Property-based tests for DirectMemory invariants using Hypothesis."""

import sys
import json
import tempfile
import os
from pathlib import Path

sys.path.insert(0, "src")

# Hypothesis is optional — skip tests if not installed
pytest_import = __import__("pytest", globals(), locals(), [], 0)
hypothesis = None
try:
    hypothesis = __import__("hypothesis", globals(), locals(), [], 0)
    from hypothesis import given, strategies as st, settings
except ImportError:
    hypothesis = None


def _with_temp_db(func):
    """Decorator to run a test with a temporary DB."""
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        old_env = os.environ.get("AZAZAEL_MEMORY_DIR", "")
        tmpdir = Path(tempfile.mkdtemp())
        os.environ["AZAZAEL_MEMORY_DIR"] = str(tmpdir)
        # Clear any cached DB path
        from CORE import direct_memory
        direct_memory.DB_PATH = direct_memory._get_db_path()
        try:
            return func(*args, **kwargs)
        finally:
            if old_env:
                os.environ["AZAZAEL_MEMORY_DIR"] = old_env
            else:
                os.environ.pop("AZAZAEL_MEMORY_DIR", None)
            # Cleanup
            import shutil
            try:
                shutil.rmtree(str(tmpdir))
            except Exception:
                pass
    return wrapper


@_with_temp_db
def test_idempotent_link():
    """Invariant: linking the same session twice is idempotent."""
    from CORE.direct_memory import DirectMemory

    dm = DirectMemory()
    dm.file_read(["test.py"], "test")

    # Link once
    r1 = dm.link_audit_session("/fake/session.jsonl", "uuid-1", mapping_type="manual")
    assert r1 is True, "First link should succeed"

    # Link again (same pair)
    r2 = dm.link_audit_session("/fake/session.jsonl", "uuid-1", mapping_type="manual")
    assert r2 is False, "Second link should be no-op (idempotent)"

    # Only one mapping should exist
    mappings = dm.list_mappings()
    assert len(mappings) == 1, f"Expected 1 mapping, got {len(mappings)}"

    dm.end()


@_with_temp_db
def test_record_then_replay():
    """Invariant: replay_session returns all recorded events in order."""
    from CORE.direct_memory import DirectMemory

    dm = DirectMemory()
    ids = []
    for i in range(5):
        ids.append(dm.record(f"test_event_{i}", {"index": i}, tags=["test"]))

    replay = DirectMemory.replay_session(dm.session_id)
    assert len(replay) >= 5, f"Expected >=5 events, got {len(replay)}"

    # Events should be in order
    replay_events = [
        e for e in replay if str(e.get("event_type", "")).startswith("test_event_")
    ]
    assert len(replay_events) >= 5, f"Expected 5 test events, got {len(replay_events)}"

    for i, ev in enumerate(replay_events[:5]):
        data = ev.get("data", {})
        index = data.get("index", -1) if isinstance(data, dict) else -1
        assert index == i, f"Expected index {i}, got {index}"

    dm.end()


@_with_temp_db
def test_purge_session():
    """Invariant: after purging a session, its events are gone."""
    from CORE.direct_memory import DirectMemory

    dm = DirectMemory()
    dm.record("test_event", {"v": 1})

    session_id = dm.session_id
    dm.end()

    # Verify events exist
    replay = DirectMemory.replay_session(session_id)
    assert len(replay) > 0, "Events should exist before purge"

    # Purge
    deleted = DirectMemory.purge_session(session_id)
    assert deleted > 0, f"Expected >0 deleted, got {deleted}"

    # Verify gone
    replay = DirectMemory.replay_session(session_id)
    assert len(replay) == 0, f"Expected 0 events after purge, got {len(replay)}"


@_with_temp_db
def test_validate_event_type():
    """Invariant: validate_event_type returns correct boolean."""
    from CORE.direct_memory import DirectMemory

    # Known valid types
    assert DirectMemory.validate_event_type("file_read") is True
    assert DirectMemory.validate_event_type("session_start") is True

    # event_type function itself always works
    dm = DirectMemory()
    result = dm.record("test_custom_type", {"v": 1})
    assert result > 0, "Should record custom types"
    dm.end()


@_with_temp_db
def test_deprecation_warnings():
    """Invariant: deprecation_warnings lists deprecated types if present."""
    from CORE.direct_memory import DirectMemory

    # Record a deprecated type
    dm = DirectMemory()
    dm.record("cluster_created", {"v": 1})
    dm.end()

    warnings = DirectMemory.deprecation_warnings()
    assert len(warnings) > 0, "Should detect deprecated cluster_created"
    assert any(w["old"] == "cluster_created" for w in warnings)


@_with_temp_db
def test_cache_invalidation():
    """Invariant: cache invalidation works after a write."""
    from CORE.direct_memory import DirectMemory

    dm = DirectMemory()
    dm.record("test_event", {"v": 1})
    dm.end()

    # Cache something
    DirectMemory._cache_set("test_key", {"data": 123}, ttl=30)
    cached = DirectMemory._cache_get("test_key")
    assert cached == {"data": 123}

    # Invalidate specific
    DirectMemory._cache_invalidate(pattern="test_key")
    cached = DirectMemory._cache_get("test_key")
    assert cached is None


@_with_temp_db
def test_time_series_empty():
    """Invariant: time_series returns list even when empty."""
    from CORE.direct_memory import DirectMemory

    # Init DB by creating a session
    dm = DirectMemory()
    dm.end()
    result = DirectMemory.time_series(granularity="day", limit=7)
    assert isinstance(result, list), f"Expected list, got {type(result)}"


@_with_temp_db
def test_filtered_query_no_params():
    """Invariant: filtered_query with no params returns recent events."""
    from CORE.direct_memory import DirectMemory

    dm = DirectMemory()
    dm.record("test_event", {"v": 1})
    dm.end()

    result = DirectMemory.filtered_query(limit=10)
    assert isinstance(result, list)
    assert len(result) > 0
