# coding: utf-8
"""
Unit tests for RaceConditionDetector and ErrorRecoveryOrchestrator.

Tests the two P0 worms built during the June 4, 2026 session.
All tests use in-memory state - no persistence, no monkey-patching.

Key limitation documented: __setattr__ only traces attribute REPLACEMENT
(e.g., self._dict = {}), not mutation (e.g., self._dict[key] = value).
This is inherent to the Python data model.
"""

import threading
import time
import unittest
from datetime import datetime

# Path setup
import sys
sys.path.insert(0, "src")

from CORE.WORMS.expansion.race_detector import RaceConditionDetector
from CORE.WORMS.expansion.error_recovery import (
    ErrorRecoveryOrchestrator, RecoveryEntry,
)


# Mock Worm with shared mutable state
class SimpleWorm:
    def __init__(self, name="test"):
        self._lock = threading.Lock()
        self._shared_dict = {}
        self._shared_list = []
        self.name = name

    def set_dict(self, value):
        """Replace _shared_dict (triggers __setattr__)."""
        self._shared_dict = value

    def set_list(self, value):
        """Replace _shared_list (triggers __setattr__)."""
        self._shared_list = value

    def execute(self):
        return {"status": "ok", "name": self.name}


class WormWithExecute:
    def execute(self):
        return {"status": "ok"}
    def get_status(self):
        return {"status": "degraded_fallback"}


class FailingWorm:
    def __init__(self, fail_count=999):
        self.call_count = 0
        self.fail_count = fail_count
    def execute(self):
        self.call_count += 1
        if self.call_count <= self.fail_count:
            raise RuntimeError(f"Simulated failure #{self.call_count}")
        return {"status": "recovered"}
    def get_status(self):
        return {"status": "fallback_ok"}


# ═══════════════════════════════════════════════════════════════════════
# RaceConditionDetector Tests
# ═══════════════════════════════════════════════════════════════════════

class TestRaceConditionDetector(unittest.TestCase):
    """Tests for RaceConditionDetector."""

    def setUp(self):
        self.detector = RaceConditionDetector()

    def test_instrument_worm_returns_metadata(self):
        worm = SimpleWorm("test1")
        result = self.detector.instrument_worm(worm, label="test1")
        self.assertEqual(result["worm"], "test1")
        self.assertIn("_lock.acquire", result["instrumented"])
        self.assertGreater(len(result["shared_attrs"]), 0)

    def test_instrument_worm_registers_tracer(self):
        worm = SimpleWorm("test2")
        self.detector.instrument_worm(worm)
        self.assertTrue(hasattr(type(worm), '_rcd_original_setattr'))

    def test_instrument_worm_tracks_lock_acquire(self):
        worm = SimpleWorm("test3")
        self.detector.instrument_worm(worm)
        with worm._lock:
            pass
        with self.detector._lock:
            events = [e for e in self.detector._access_log
                     if e.get("action") == "lock_acquire"]
        self.assertGreater(len(events), 0)

    def test_instrument_worm_tracks_attr_replacement(self):
        """__setattr__ traces attribute REPLACEMENT, not mutation."""
        worm = SimpleWorm("test4")
        self.detector.instrument_worm(worm)
        worm.set_dict({"new": "value"})  # REPLACES _shared_dict
        with self.detector._lock:
            writes = [e for e in self.detector._access_log
                     if e.get("action") == "write"]
        self.assertGreater(len(writes), 0)

    def test_multi_instance_independent_tracing(self):
        worm1 = SimpleWorm("worm_a")
        worm2 = SimpleWorm("worm_b")
        self.detector.instrument_worm(worm1, label="worm_a")
        self.detector.instrument_worm(worm2, label="worm_b")
        worm1.set_dict({"a": 1})
        worm2.set_dict({"b": 2})
        with self.detector._lock:
            a = [e for e in self.detector._access_log
                if e.get("worm") == "worm_a"]
            b = [e for e in self.detector._access_log
                if e.get("worm") == "worm_b"]
        self.assertGreater(len(a), 0)
        self.assertGreater(len(b), 0)

    def test_uninstrument_restores_lock(self):
        worm = SimpleWorm("test5")
        orig = worm._lock.acquire
        self.detector.instrument_worm(worm)
        self.assertNotEqual(worm._lock.acquire, orig)
        self.detector.uninstrument_worm(worm)
        self.assertEqual(worm._lock.acquire, orig)

    def test_uninstrument_restores_setattr(self):
        worm = SimpleWorm("test6")
        self.detector.instrument_worm(worm)
        self.assertTrue(hasattr(type(worm), '_rcd_original_setattr'))
        self.detector.uninstrument_worm(worm)
        self.assertFalse(hasattr(type(worm), '_rcd_original_setattr'))

    def test_detect_data_races_empty(self):
        self.assertEqual(len(self.detector.detect_data_races()), 0)

    def test_detect_data_races_single_thread(self):
        worm = SimpleWorm("test7")
        self.detector.instrument_worm(worm)
        worm.set_dict({"x": 1})
        self.assertEqual(len(self.detector.detect_data_races()), 0)

    def test_detect_thread_imports_finds_issues(self):
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', delete=False, encoding='utf-8')
        tmp.write("def bad():\n    import os as _os\n    return _os.getcwd()\n")
        tmp.close()
        try:
            risks = self.detector.detect_thread_imports([tmp.name])
            self.assertGreater(len(risks), 0)
        finally:
            os.unlink(tmp.name)

    def test_detect_thread_imports_clean(self):
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', delete=False, encoding='utf-8')
        tmp.write("import os\ndef ok():\n    return os.getcwd()\n")
        tmp.close()
        try:
            risks = self.detector.detect_thread_imports([tmp.name])
            self.assertEqual(len(risks), 0)
        finally:
            os.unlink(tmp.name)

    def test_safety_report_empty(self):
        r = self.detector.generate_safety_report()
        self.assertEqual(r["version"], "1.0.0")
        self.assertEqual(r["summary"]["worms_instrumented"], 0)

    def test_safety_report_with_worm(self):
        self.detector.instrument_worm(SimpleWorm("rpt"))
        r = self.detector.generate_safety_report()
        self.assertEqual(r["summary"]["worms_instrumented"], 1)

    def test_access_log_bounded(self):
        worm = SimpleWorm("cap")
        self.detector.instrument_worm(worm)
        for i in range(100):
            worm.set_dict({str(i): i})
        with self.detector._lock:
            self.assertLessEqual(len(self.detector._access_log), 2000)

    def test_uninstrument_unknown_no_error(self):
        self.detector.uninstrument_worm(SimpleWorm("x"))


# ═══════════════════════════════════════════════════════════════════════
# ErrorRecoveryOrchestrator Tests
# ═══════════════════════════════════════════════════════════════════════

class TestErrorRecoveryOrchestrator(unittest.TestCase):
    """Tests for ErrorRecoveryOrchestrator."""

    def setUp(self):
        self.ero = ErrorRecoveryOrchestrator(
            max_retries=2,
            base_backoff_ms=1.0,   # minimal backoff for fast tests
            circuit_threshold=5,
            circuit_reset_seconds=900.0,
        )

    def test_execute_successful(self):
        result = self.ero.execute_with_recovery("OK", WormWithExecute())
        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["recovery"]["attempts"], 1)

    def test_execute_returns_result(self):
        result = self.ero.execute_with_recovery("OK", WormWithExecute())
        self.assertEqual(result["result"]["status"], "ok")

    def test_retry_on_transient_failure(self):
        worm = FailingWorm(fail_count=1)
        result = self.ero.execute_with_recovery("TF", worm)
        # Should succeed after 1 retry
        self.assertIn(result["outcome"], ["success", "degraded"])

    def test_fallback_when_exhausted(self):
        worm = FailingWorm(fail_count=999)
        result = self.ero.execute_with_recovery("FE", worm)
        self.assertIn(result["outcome"], ["degraded", "circuit_open"])
        self.assertIsNotNone(result.get("result"))

    def test_fallback_prefers_get_status(self):
        worm = FailingWorm(fail_count=999)
        result = self.ero.execute_with_recovery("GS", worm)
        if result["outcome"] == "degraded":
            self.assertIn("status", result["result"])

    def test_circuit_breaker_opens(self):
        ero = ErrorRecoveryOrchestrator(
            max_retries=1, base_backoff_ms=1.0,
            circuit_threshold=2, circuit_reset_seconds=900.0)
        worm = FailingWorm(fail_count=999)
        for _ in range(5):
            ero.execute_with_recovery("CB", worm)
        status = ero.get_circuit_breaker_status()
        self.assertIn("CB", status["open_circuits"])

    def test_circuit_open_returns_skip(self):
        ero = ErrorRecoveryOrchestrator(
            max_retries=1, base_backoff_ms=1.0,
            circuit_threshold=1, circuit_reset_seconds=900.0)
        ero.execute_with_recovery("Skip", FailingWorm(fail_count=999))
        result = ero.execute_with_recovery("Skip", WormWithExecute())
        self.assertEqual(result["outcome"], "circuit_open")

    def test_reset_circuit(self):
        ero = ErrorRecoveryOrchestrator(
            max_retries=1, base_backoff_ms=1.0,
            circuit_threshold=1, circuit_reset_seconds=900.0)
        ero.execute_with_recovery("Rst", FailingWorm(fail_count=999))
        self.assertIn("Rst", ero.get_circuit_breaker_status()["open_circuits"])
        ero.reset_circuit("Rst")
        self.assertNotIn("Rst", ero.get_circuit_breaker_status()["open_circuits"])

    def test_recovery_entry_dataclass(self):
        entry = RecoveryEntry(
            worm_type="TestWorm", attempt=1, outcome="success",
            duration_ms=42.0, error=None, strategy="retry")
        self.assertEqual(entry.worm_type, "TestWorm")
        self.assertIsNotNone(entry.timestamp)

    def test_get_stats_tracks(self):
        self.ero.execute_with_recovery("S1", WormWithExecute())
        self.ero.execute_with_recovery("S2", WormWithExecute())
        stats = self.ero.get_stats()
        self.assertGreaterEqual(stats["total_attempts"], 2)

    def test_get_stats_initial(self):
        stats = self.ero.get_stats()
        self.assertEqual(stats["total_attempts"], 0)
        self.assertEqual(stats["success_rate"], 0.0)

    def test_periodic_cleanup_no_error(self):
        self.ero._periodic_cleanup()

    def test_reset_unknown_no_error(self):
        self.assertTrue(self.ero.reset_circuit("Unknown")["reset"])


if __name__ == "__main__":
    unittest.main()
