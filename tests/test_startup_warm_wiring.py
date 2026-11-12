# coding: utf-8
"""
Tests for startup_warm wiring in DirectMemory.__init__.

Verifies that _prewarm_registry() is called during init, is non-blocking,
handles import errors gracefully, and doesn't interfere with session recording.

Functions tested indirectly:
    - DirectMemory.__init__()     calls _prewarm_registry
    - DirectMemory._prewarm_registry()  try/except import + call
    - startup_warm.ensure_registry_warm()  non-blocking warm start
"""

import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.env  # type: ignore[import-not-found]

# Ceiling for "the warm call did not block", not a performance budget. A
# blocked init shows up as seconds-to-minutes, never as tens of milliseconds.
PREWARM_HANG_CEILING_S = 5.0

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ── DirectMemory init timing ───────────────────────────────────────


class TestDirectMemoryInitTiming:
    """Verify DirectMemory init is non-blocking (< 100ms steady-state).

    Contract note (profiled Sept 2026): DirectMemory.__init__ itself costs
    ~12ms. The FIRST init in a process additionally pays the one-time lazy
    import chain in _prewarm_registry() (CORE.startup_warm ~330ms +
    CORE.worm_registry ~230ms cold). That cold cost is covered by
    TestDirectMemoryColdInit; the tests below assert the steady-state
    per-process contract.
    """

    def test_init_is_fast(self):
        """Steady-state init should complete in < 100ms despite prewarm call."""
        from CORE.direct_memory import DirectMemory

        # Warm the process once (absorbs one-time lazy imports).
        DirectMemory().end({"warmup": "init_timing"})

        t0 = time.perf_counter()
        dm = DirectMemory()
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.1, (
            f"DirectMemory init took {elapsed*1000:.1f}ms, expected < 100ms"
        )
        assert dm.session_id is not None
        dm.end({"test": "init_timing"})

    def test_recording_works_immediately(self):
        """Session recording should work immediately after init (non-blocking)."""
        from CORE.direct_memory import DirectMemory

        dm = DirectMemory()

        t0 = time.perf_counter()
        row_id = dm.record("test_event", {"purpose": "immediate_recording"})
        elapsed = time.perf_counter() - t0

        assert row_id > 0, f"Expected valid row_id, got {row_id}"
        assert elapsed < 0.05, (
            f"First record took {elapsed*1000:.1f}ms, expected < 50ms"
        )
        dm.end({"test": "immediate_recording"})

    def test_multiple_records_no_blocking(self):
        """Multiple sequential records should all be fast."""
        from CORE.direct_memory import DirectMemory

        dm = DirectMemory()

        times = []
        for i in range(10):
            t0 = time.perf_counter()
            dm.record(f"bulk_event_{i}", {"n": i})
            times.append(time.perf_counter() - t0)

        max_time = max(times)
        avg_time = sum(times) / len(times)

        assert max_time < 0.05, (
            f"Slowest record took {max_time*1000:.1f}ms, expected < 50ms"
        )
        assert avg_time < 0.02, (
            f"Average record time {avg_time*1000:.1f}ms, expected < 20ms"
        )
        dm.end({"test": "bulk_recording", "count": 10})

    def test_init_with_custom_session_id(self):
        """DirectMemory with custom session_id should still be fast."""
        from CORE.direct_memory import DirectMemory

        # Steady-state guard: previous tests warmed the process, but make
        # the contract explicit in case of test reordering.
        DirectMemory().end({"warmup": "custom_session"})

        t0 = time.perf_counter()
        dm = DirectMemory(session_id="test_warm_wiring_custom")
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.1
        assert "test_warm_wiring_custom" in dm.session_id
        dm.end({"test": "custom_session"})

    def test_init_with_merged_session(self):
        """DirectMemory with merged_session_id should be fast and reuse session."""
        from CORE.direct_memory import DirectMemory

        # Steady-state guard (see class docstring).
        DirectMemory().end({"warmup": "merged_session"})

        # Create a primary session first
        t0 = time.perf_counter()
        dm = DirectMemory(session_id="test_warm_wiring_merged", merged_session_id="parent_session_123")
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.2
        # Should create a message_start event instead of session_start
        assert dm.count() >= 1
        dm.end({"test": "merged_session"})

    def test_cold_init_budget(self):
        """First init in a fresh process has an explicit cold-start budget.

        Regression guard for the Sept 2026 timing fix: the one-time lazy
        import chain (CORE.startup_warm + CORE.worm_registry) dominated
        init at ~274-340ms cold vs ~12ms steady-state. Budget: 1500ms cold.
        Steady-state contract is covered by test_init_is_fast.
        """
        from CORE.direct_memory import DirectMemory

        t0 = time.perf_counter()
        dm = DirectMemory()
        elapsed = time.perf_counter() - t0

        # Generous cold budget: even a fully cold import chain sits well
        # under this ceiling. A regression back to unconditional full-DB
        # migration (~270ms extra) or a new synchronous warm dependency
        # would blow past it.
        assert elapsed < 1.5, (
            f"Cold DirectMemory init took {elapsed*1000:.1f}ms, budget 1500ms"
        )
        assert dm.session_id is not None
        dm.end({"test": "cold_init_budget"})


# ── _prewarm_registry error handling ───────────────────────────────


class TestPrewarmErrorHandling:
    """Verify _prewarm_registry handles errors gracefully."""

    def test_prewarm_catches_import_error(self):
        """_prewarm_registry should silently catch ImportError."""
        from CORE.direct_memory import DirectMemory

        dm = DirectMemory()

        # Monkey-patch to test error handling: make startup_warm import fail
        original_imports = {}

        def broken_import(name, *args, **kwargs):
            if "startup_warm" in name:
                raise ImportError(f"Simulated import failure for {name}")
            return __import__(name, *args, **kwargs)

        import builtins
        original_import = builtins.__import__
        try:
            builtins.__import__ = broken_import
            # Should not raise — bare except catches everything
            dm._prewarm_registry()
        finally:
            builtins.__import__ = original_import

        # Session should still work
        row_id = dm.record("test_post_error", {"note": "after simulated import failure"})
        assert row_id > 0
        dm.end({"test": "import_error_handling"})

    def test_prewarm_catches_general_exception(self):
        """_prewarm_registry should silently catch any Exception (not just ImportError)."""
        from CORE.direct_memory import DirectMemory

        dm = DirectMemory()

        # Simulate ensure_registry_warm raising an error
        import CORE.startup_warm as sw
        original_func = sw.ensure_registry_warm

        def broken_warm():
            raise RuntimeError("Simulated warm failure")

        try:
            sw.ensure_registry_warm = broken_warm
            # Should not raise — bare except catches everything
            dm._prewarm_registry()
        finally:
            sw.ensure_registry_warm = original_func

        # Session should still work
        row_id = dm.record("test_post_runtime_error", {"note": "after simulated runtime error"})
        assert row_id > 0
        dm.end({"test": "runtime_error_handling"})

    def test_prewarm_does_not_block_on_missing_module(self):
        """Init must return when startup_warm raises — a hang ceiling, not a stopwatch.

        The contract in direct_memory._prewarm_registry is "never raise and
        never block": the warm call is attempted and any Exception is recorded
        rather than propagated. This test used to assert ``elapsed < 0.1`` from
        a SINGLE sample, which measures the machine's load rather than the code
        — it failed at 13.4s inside a full-suite run (96 processes on 16 cores)
        while passing at ~30ms alone, and was green alone and under 12 CPU
        burners (measured 2026-11-04). The invariants below are load-independent:
        the patched warm path was actually exercised, the ImportError did not
        escape, and init returned well inside a hang ceiling.
        """
        from unittest.mock import patch
        from CORE.direct_memory import DirectMemory

        attempts = []

        def _broken_warm():
            attempts.append(1)
            raise ImportError("Simulated missing module")

        t0 = time.perf_counter()
        with patch("CORE.startup_warm.ensure_registry_warm", side_effect=_broken_warm):
            dm = DirectMemory()
            elapsed = time.perf_counter() - t0
        try:
            assert attempts == [1], "the patched warm path was never called"
            assert elapsed < PREWARM_HANG_CEILING_S, (
                f"Init with a failing startup_warm took {elapsed:.1f}s — "
                "the warm call blocked instead of failing fast"
            )
            assert dm.session_id is not None
            row_id = dm.record("test_missing_module", {"note": "init survived"})
            assert row_id > 0
        finally:
            dm.end({"test": "missing_module"})


# ── startup_warm integration ───────────────────────────────────────


class TestStartupWarmIntegration:
    """Verify ensure_registry_warm is called and is non-blocking."""

    def test_ensure_registry_warm_is_callable(self):
        """ensure_registry_warm should be importable and callable."""
        from CORE.startup_warm import ensure_registry_warm, warm_caches_ready

        # Should not raise
        result = ensure_registry_warm()
        assert isinstance(result, bool)

    def test_ensure_registry_warm_returns_within_timeout(self):
        """ensure_registry_warm should return quickly (non-blocking).

        Uses warm_caches_now() for a clean timing measurement without
        resetting module-level state.
        """
        from CORE.startup_warm import warm_caches_now

        t0 = time.perf_counter()
        result = warm_caches_now()  # Synchronous, guaranteed fresh measurement
        elapsed = time.perf_counter() - t0

        # warm_caches_now calls discover_worms which can take 30-45s on cold start
        # but with the pickle cache it should be fast
        assert isinstance(result, bool)

    def test_warm_caches_ready_returns_bool(self):
        """warm_caches_ready should always return a bool without error."""
        from CORE.startup_warm import warm_caches_ready

        result = warm_caches_ready()
        assert isinstance(result, bool)

    def test_multiple_calls_to_ensure_registry_warm(self):
        """Multiple calls should be safe (lock prevents duplicate threads)."""
        from CORE.startup_warm import ensure_registry_warm, warm_caches_ready

        for _ in range(5):
            result = ensure_registry_warm()
            assert isinstance(result, bool)

    def test_direct_memory_calls_prewarm_on_init(self):
        """Verify _prewarm_registry is called during DirectMemory init."""
        from unittest.mock import patch
        from CORE.direct_memory import DirectMemory

        with patch.object(DirectMemory, "_prewarm_registry", wraps=None) as mock_prewarm:
            dm = DirectMemory()
            # _prewarm_registry should have been called exactly once during __init__
            assert mock_prewarm.call_count == 1, (
                f"Expected _prewarm_registry called once, got {mock_prewarm.call_count}"
            )
            dm.end({"test": "prewarm_called_on_init"})


# ── cache_status() tests ───────────────────────────────────────────


class TestCacheStatus:
    """Verify the cache_status() convenience function reports correctly."""

    def test_cache_status_returns_dict_with_expected_keys(self):
        """cache_status() should return a dict with all 4 keys."""
        from CORE.startup_warm import cache_status

        status = cache_status()

        assert isinstance(status, dict)
        assert "ready" in status
        assert "registry_size" in status
        assert "last_warm_time_ago" in status
        assert "thread_alive" in status

    def test_cache_status_ready_is_bool(self):
        """'ready' key should be a boolean."""
        from CORE.startup_warm import cache_status

        status = cache_status()
        assert isinstance(status["ready"], bool)

    def test_cache_status_registry_size_is_int(self):
        """'registry_size' key should be an integer."""
        from CORE.startup_warm import cache_status

        status = cache_status()
        assert isinstance(status["registry_size"], int)
        assert status["registry_size"] >= 0

    def test_cache_status_last_warm_time_is_float_or_none(self):
        """'last_warm_time_ago' should be a float or None."""
        from CORE.startup_warm import cache_status

        status = cache_status()
        assert status["last_warm_time_ago"] is None or isinstance(status["last_warm_time_ago"], float)

    def test_cache_status_thread_alive_is_bool(self):
        """'thread_alive' key should be a boolean."""
        from CORE.startup_warm import cache_status

        status = cache_status()
        assert isinstance(status["thread_alive"], bool)

    def test_cache_status_after_warm_caches_now(self):
        """After warm_caches_now(), status should show ready=True and registry_size > 0."""
        from CORE.startup_warm import cache_status, warm_caches_now

        # Force a synchronous warm
        warm_caches_now()

        status = cache_status()
        assert status["ready"] is True, "Expected ready=True after warm_caches_now()"
        assert status["registry_size"] > 0, (
            f"Expected registry_size > 0 after warm, got {status['registry_size']}"
        )
        assert status["last_warm_time_ago"] is not None, "Expected last_warm_time after warm"
        assert isinstance(status["last_warm_time_ago"], float)
        # Should be a recent warm (within the last 60 seconds)
        assert status["last_warm_time_ago"] < 60.0, (
            f"Expected last_warm_time_ago < 60s, got {status['last_warm_time_ago']:.1f}s"
        )

    def test_cache_status_thread_alive_false_after_sync_warm(self):
        """After synchronous warm_caches_now(), the warm thread should not be alive."""
        from CORE.startup_warm import cache_status, warm_caches_now

        warm_caches_now()
        status = cache_status()
        # After synchronous warm, the worker function completes before returning,
        # so no thread should be alive
        assert status["thread_alive"] is False

    def test_cache_status_consistent_calls(self):
        """Multiple calls to cache_status() should all return consistent shapes."""
        from CORE.startup_warm import cache_status

        for _ in range(5):
            s = cache_status()
            assert isinstance(s["ready"], bool)
            assert isinstance(s["registry_size"], int)
            assert isinstance(s["thread_alive"], bool)
            assert s["last_warm_time_ago"] is None or isinstance(s["last_warm_time_ago"], float)

    def test_cache_status_reports_size_change_after_discovery(self):
        """cache_status().registry_size should match WORM_REGISTRY size."""
        from CORE.startup_warm import cache_status, warm_caches_now
        from CORE.worm_registry import WORM_REGISTRY

        warm_caches_now()
        status = cache_status()

        # Registry size means worm count (fixed 2026-09-29: both sides
        # previously summed info-field counts, passing on a wrong property).
        expected = len(WORM_REGISTRY) if WORM_REGISTRY else 0
        assert status["registry_size"] == expected, (
            f"cache_status reports {status['registry_size']} but WORM_REGISTRY has {expected}"
        )

    def test_ensure_registry_warm_does_not_validate_on_the_caller(self, monkeypatch):
        """MEASURED DEFECT. `ensure_registry_warm()` is documented as
        "non-blocking if already warm", and its warm branch called
        `_run_registry_validation()` INLINE. Observed: a warm-up
        `DirectMemory()` returned in 71 ms and the very next one blocked for
        1837 ms, because the background warm thread had populated
        `WORM_REGISTRY` in between -- so whether `__init__` met its <100 ms
        contract depended on a race with another thread."""
        import CORE.startup_warm as sw
        import CORE.worm_registry as wr

        monkeypatch.setattr(wr, "WORM_REGISTRY",
                            {"A": {"category": "utility", "priority": 5}})
        monkeypatch.setattr(sw, "_warmed", threading.Event())
        monkeypatch.setattr(sw, "_validation_started", threading.Event())
        monkeypatch.setattr(sw, "_validation_done", threading.Event())

        # Which THREAD ran the validation, not merely whether it ran: the whole
        # defect was that this branch ran it on the CALLER, and a worker thread
        # doing it is the fix, not the bug.
        ran_on = []
        monkeypatch.setattr(sw, "_run_registry_validation",
                            lambda *a, **k: ran_on.append(
                                threading.current_thread().name))
        started = []
        real_async = sw._ensure_registry_validation_async
        monkeypatch.setattr(sw, "_ensure_registry_validation_async",
                            lambda: (started.append(1), real_async())[1])

        t0 = time.perf_counter()
        assert sw.ensure_registry_warm() is True
        elapsed = time.perf_counter() - t0

        assert started == [1], "the warm path must hand validation off"
        # THE assertion: which THREAD ran it. A regression back to inline
        # validation would run the (patched) body on this thread and fail here.
        assert threading.current_thread().name not in ran_on, (
            "the caller ran the validation inline: %s" % ran_on)
        # A loose ceiling only, because `Thread.start()` blocks until the worker
        # is running and measured ~100 ms on this free-threaded build -- the
        # inline regression this guards against cost 1837 ms.
        assert elapsed < 0.5, (
            f"warm registry path took {elapsed*1000:.1f}ms on the caller"
        )

    def test_registry_validation_runs_once_unless_forced(self, monkeypatch):
        """It ran on EVERY call, so a hot path calling it once per
        `DirectMemory()` paid the full 232-entry walk every time -- and the cost
        was indistinguishable from general slowness."""
        import CORE.startup_warm as sw
        import CORE.worm_registry as wr

        monkeypatch.setattr(sw, "_validation_started", threading.Event())
        monkeypatch.setattr(sw, "_validation_done", threading.Event())
        monkeypatch.setattr(sw, "_registry_validation", None)
        calls = []
        monkeypatch.setattr(wr, "validate_registry_importable",
                            lambda: (calls.append(1), {"valid": True, "total": 1,
                                                       "checked": 1,
                                                       "broken": []})[1])

        sw._run_registry_validation()
        sw._run_registry_validation()
        assert len(calls) == 1, "an unforced call must reuse the stored result"

        sw._run_registry_validation(force=True)
        assert len(calls) == 2, "an explicit re-run must actually re-run"

    def test_the_async_starter_launches_at_most_one_validation(self, monkeypatch):
        """A second caller must not queue a second 232-entry walk behind the
        first; the guard is the Started event, not a thread's liveness."""
        import CORE.startup_warm as sw

        monkeypatch.setattr(sw, "_validation_started", threading.Event())
        monkeypatch.setattr(sw, "_validation_done", threading.Event())

        assert sw._ensure_registry_validation_async() is True
        assert sw._ensure_registry_validation_async() is False
        sw._validation_done.set()
        assert sw._ensure_registry_validation_async() is False

    def test_cache_status_after_warm_caches_ready_before_warm(self):
        """cache_status should show ready=False before any warm has completed.

        This test verifies the initial state before any warming occurs,
        distinguishing it from the post-warm state tested above.
        """
        from CORE.startup_warm import cache_status, warm_caches_ready

        # Check if we can observe the not-yet-warm state
        # (if already warm from another test, this tests the warm state instead)
        if not warm_caches_ready():
            status = cache_status()
            assert status["ready"] is False
            assert status["registry_size"] == 0
            # last_warm_time_ago should be None since warm never completed
            assert status["last_warm_time_ago"] is None
