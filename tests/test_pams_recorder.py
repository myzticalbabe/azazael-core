#!/usr/bin/env python3
"""Tests for PamsRecorder — lazy LocalMemoryStore init, singleton, thread safety."""

import os
import sys
import time
import json
import threading
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages/azazael-core/src"))

import pytest


class TestPamsRecorderSingleton:
    """PamsRecorder is a singleton — verify identity via its accessor.

    2026-09-21: these two tests used `PamsRecorder()` directly and asserted the
    CONSTRUCTOR was the singleton, which the class does not and cannot honour:
    four lazy-init tests in this same file require a freshly constructed recorder
    to have `_local_store_init_attempted is False`, which is only true for a new
    object. Two contradictory contracts, four tests to one, and the class ships a
    dedicated accessor — so the accessor is the contract. `get_recorder()` has done
    double-checked locking since the June 11 2026 P0 fix; these tests now exercise
    the thing that provides the guarantee.

    `test_session_id_persists` was worse than failing: it PASSED against two
    distinct objects, because `session_id = uuid7().hex[:8]` takes its first 8 hex
    characters from the timestamp, so two constructions in the same millisecond
    share a prefix. It was asserting a clock, not persistence. Both now go through
    `get_recorder()`, where the assertion is about the object.
    """

    def setup_method(self):
        from CORE.PamsRecorder import PamsRecorder
        PamsRecorder.reset_for_testing()

    def teardown_method(self):
        from CORE.PamsRecorder import PamsRecorder
        PamsRecorder.reset_for_testing()

    def test_same_instance(self):
        """get_recorder() twice should return the same instance."""
        from CORE.PamsRecorder import get_recorder
        assert get_recorder() is get_recorder()

    def test_session_id_persists(self):
        """Session ID should be stable across references to the singleton."""
        from CORE.PamsRecorder import get_recorder
        p1 = get_recorder()
        sid = p1.session_id
        assert get_recorder().session_id == sid


class TestPamsRecorderLazyInit:
    """Verify _ensure_local_store() is deferred until first use."""

    def setup_method(self):
        from CORE.PamsRecorder import PamsRecorder
        PamsRecorder.reset_for_testing()

    def teardown_method(self):
        from CORE.PamsRecorder import PamsRecorder
        PamsRecorder.reset_for_testing()

    def test_init_does_not_init_local_store(self):
        """After construction, _local_ok should be False (not yet attempted)."""
        from CORE.PamsRecorder import PamsRecorder
        p = PamsRecorder()
        assert p._local_ok is False
        assert p._local_store_init_attempted is False

    def test_record_event_triggers_lazy_init(self):
        """record_event() should trigger _ensure_local_store() on first call."""
        from CORE.PamsRecorder import PamsRecorder
        p = PamsRecorder()
        # After reset_for_testing(), flags are fresh
        assert p._local_store_init_attempted is False

        p.record_event("test", {"msg": "hello"}, source="test", severity="info")
        assert p._local_store_init_attempted is True

    def test_lazy_init_only_happens_once(self):
        """Second call to record_event should not re-attempt local store init."""
        from CORE.PamsRecorder import PamsRecorder
        p = PamsRecorder()
        assert p._local_store_init_attempted is False

        # First call triggers init attempt
        p.record_event("test", {"n": 1}, source="test")

        # Second call should NOT re-attempt (flag already True)
        p.record_event("test", {"n": 2}, source="test")
        assert p._local_store_init_attempted is True

    def test_record_event_succeeds_without_local_store(self):
        """record_event should succeed (write to fallback buffer) even without local store."""
        from CORE.PamsRecorder import PamsRecorder
        p = PamsRecorder()
        # After reset, local store is not yet attempted
        result = p.record_event("test_offline", {"x": 42}, source="pytest", severity="info")
        assert isinstance(result, bool)
        assert p.event_count > 0

    def test_record_event_writes_to_fallback_buffer(self):
        """Without local store, events should go to the in-memory fallback buffer."""
        from CORE.PamsRecorder import PamsRecorder
        p = PamsRecorder()
        # Force fallback mode: skip lazy init
        p._local_store_init_attempted = True
        p._local_ok = False
        p._local_store = None
        buf_len_before = len(p._fallback_buffer)

        p.record_event("buffer_test", {"n": 1}, source="pytest")
        assert len(p._fallback_buffer) == buf_len_before + 1


class TestPamsRecorderEventRecording:
    """Verify events are actually recorded with correct structure."""

    def setup_method(self):
        from CORE.PamsRecorder import PamsRecorder
        PamsRecorder.reset_for_testing()

    def teardown_method(self):
        from CORE.PamsRecorder import PamsRecorder
        PamsRecorder.reset_for_testing()

    def test_event_has_expected_fields(self):
        """Recorded event dict should have timestamp, event_type, source, etc."""
        from CORE.PamsRecorder import PamsRecorder
        p = PamsRecorder()
        # Force fallback mode so event goes to buffer
        p._local_store_init_attempted = True
        p._local_ok = False
        buf_len = len(p._fallback_buffer)

        p.record_event("field_test", {"k": "v"}, source="pytest", severity="warning")

        assert len(p._fallback_buffer) == buf_len + 1
        evt = p._fallback_buffer[-1]
        assert evt["event_type"] == "field_test"
        assert evt["source"] == "pytest"
        assert evt["severity"] == "warning"
        assert "timestamp" in evt
        assert evt["data"] == {"k": "v"}

    def test_query_memories_triggers_lazy_init(self):
        """query_memories() should also trigger _ensure_local_store()."""
        from CORE.PamsRecorder import PamsRecorder
        p = PamsRecorder()
        assert p._local_store_init_attempted is False

        results = p.query_memories(source="nonexistent")
        assert p._local_store_init_attempted is True
        assert isinstance(results, list)

    def test_search_memories_triggers_lazy_init(self):
        """search_memories() should also trigger _ensure_local_store()."""
        from CORE.PamsRecorder import PamsRecorder
        p = PamsRecorder()
        assert p._local_store_init_attempted is False

        results = p.search_memories("nonexistent")
        assert p._local_store_init_attempted is True
        assert isinstance(results, list)


class TestPamsRecorderThreadSafety:
    """Verify _ensure_local_store() is thread-safe under concurrent access."""

    def setup_method(self):
        from CORE.PamsRecorder import PamsRecorder
        PamsRecorder.reset_for_testing()

    def teardown_method(self):
        from CORE.PamsRecorder import PamsRecorder
        PamsRecorder.reset_for_testing()

    def test_concurrent_lazy_init(self):
        """Multiple threads calling record_event simultaneously should not crash."""
        from CORE.PamsRecorder import PamsRecorder
        p = PamsRecorder()
        assert p._local_store_init_attempted is False

        errors = []

        def hammer():
            for i in range(20):
                try:
                    p.record_event("thread_test", {"i": i}, source=f"thread_{threading.get_ident()}")
                except Exception as e:
                    errors.append(str(e))

        threads = [threading.Thread(target=hammer, daemon=True) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert p.event_count >= 1

    def test_reset_is_idempotent(self):
        """Calling reset_for_testing() multiple times should not raise."""
        from CORE.PamsRecorder import PamsRecorder
        PamsRecorder.reset_for_testing()
        PamsRecorder.reset_for_testing()
        PamsRecorder.reset_for_testing()
        p = PamsRecorder()
        assert p._local_store_init_attempted is False
        assert p._local_ok is False


if __name__ == "__main__":
    p = f = 0
    for name, obj in sorted(globals().items()):
        if isinstance(obj, type) and name.startswith("Test"):
            inst = obj()
            for mn in sorted(dir(inst)):
                if mn.startswith("test_"):
                    try:
                        if hasattr(inst, "setup_method"):
                            inst.setup_method()
                        getattr(inst, mn)()
                        if hasattr(inst, "teardown_method"):
                            inst.teardown_method()
                        print(f"  [OK] {name}.{mn}")
                        p += 1
                    except Exception as e:
                        print(f"  [FAIL] {name}.{mn}: {e}")
                        import traceback
                        traceback.print_exc()
                        f += 1
    print(f"\n  Passed: {p}, Failed: {f}")
