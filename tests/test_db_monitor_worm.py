# coding: utf-8
"""
Unit tests for DatabaseMonitorWorm and ScheduledDbMonitorWorm.

Uses store injection to test against isolated temporary databases,
completely avoiding the production LocalMemoryStore at ~/.azazael/.

Run with:
    python -m pytest tests/test_db_monitor_worm.py -v
    python tests/test_db_monitor_worm.py  # Direct runner
"""

import sys
import os
import time
import json
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

# Ensure src/ is on path
_src_dir = Path(__file__).resolve().parent.parent / "src"
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from CORE.local_memory import LocalMemoryStore

try:
    import pytest
except ImportError:                                     # direct runner: no pytest
    pytest = None


if pytest is not None:
    @pytest.fixture(autouse=True)
    # start of the recorder guard release
    #
    # _seed_store() writes 58 rows per fixture. The recorder guard is a
    # process-wide flood valve (8 rows/s, burst 200), so the first class to seed
    # spends the bucket and every later class reads back a truncated store --
    # measured 2026-11-02: "6 not greater than 30" and "5 not greater than 20"
    # in a whole-file run, both green when the file is run alone. These tests
    # check the monitor's arithmetic over the rows it was given, so they have to
    # be given all of them.
    def _recorder_guard_released(monkeypatch):
        from CORE import recorder_guard
        monkeypatch.setattr(recorder_guard, "_DISABLED", True, raising=False)
        recorder_guard.guard().reset()


def _make_temp_store() -> LocalMemoryStore:
    """Create a temporary LocalMemoryStore for testing."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store = LocalMemoryStore(db_path=tmp.name)
    store.stop_auto_vacuum()
    return store


def _destroy_temp_store(store: LocalMemoryStore):
    """Clean up a temporary store."""
    db_path = store.db_path
    store.close()
    try:
        os.unlink(db_path)
    except (OSError, FileNotFoundError):
        pass


def _seed_store(store: LocalMemoryStore, errors: int = 3, warnings: int = 5,
                infos: int = 50, sessions: int = 1):
    """Populate a store with sample data for testing."""
    for i in range(infos):
        store.record_event(
            "TestSource", "test_event",
            {"index": i, "value": i * 10},
            severity="info", tags=["test", "seed"],
        )
    for i in range(warnings):
        store.record_event(
            "TestSource", "test_warning",
            {"index": i, "msg": "warning event"},
            severity="warning", tags=["test", "warning"],
        )
    for i in range(errors):
        store.record_event(
            "TestSource", "test_error",
            {"index": i, "msg": "error event"},
            severity="error", tags=["test", "error"],
        )
    for i in range(sessions):
        store.start_session(source="test_session", metadata={"test": True})


class TestDatabaseMonitorWorm(unittest.TestCase):
    """Unit tests for DatabaseMonitorWorm with store injection."""

    def setUp(self):
        """Create a temp store and inject it into the monitor."""
        self.store = _make_temp_store()
        _seed_store(self.store, errors=3, warnings=5, infos=50, sessions=1)

        from CORE.WORMS.expansion.db_monitor import DatabaseMonitorWorm
        self.monitor = DatabaseMonitorWorm(store=self.store)

    def tearDown(self):
        """Clean up temp store."""
        _destroy_temp_store(self.store)

    def test_execute_returns_dict_with_all_keys(self):
        """execute() should return a dict with all expected keys."""
        result = self.monitor.execute()
        self.assertIsInstance(result, dict)
        expected_keys = [
            "healthy", "db_size_mb", "total_memories", "total_sessions",
            "memory_growth_24h", "error_count_7d", "warning_count_7d",
            "sources_count", "event_types_count", "auto_vacuum_running",
            "storage_backend", "alerts", "total_alerts", "timestamp",
        ]
        for key in expected_keys:
            self.assertIn(key, result, f"Missing key: {key}")

    def test_healthy_with_normal_data(self):
        """With normal data, the monitor should report healthy."""
        result = self.monitor.execute()
        self.assertTrue(result["healthy"])
        self.assertGreater(result["total_memories"], 0)
        self.assertGreater(result["total_sessions"], 0)

    def test_error_count_matches_seeded(self):
        """Error events should be reflected in error_count_7d."""
        result = self.monitor.execute()
        self.assertGreaterEqual(result["error_count_7d"], 3)

    def test_warning_count_matches_seeded(self):
        """Warning events should be reflected in warning_count_7d."""
        result = self.monitor.execute()
        self.assertGreaterEqual(result["warning_count_7d"], 5)

    def test_db_size_is_positive(self):
        """db_size_mb should be a valid positive number."""
        result = self.monitor.execute()
        self.assertGreater(result["db_size_mb"], 0)

    def test_storage_backend_is_sqlite(self):
        """storage_backend should be 'sqlite' by default."""
        result = self.monitor.execute()
        self.assertEqual(result["storage_backend"], "sqlite")

    def test_get_db_summary(self):
        """get_db_summary() should return a concise summary dict."""
        summary = self.monitor.get_db_summary()
        self.assertIsInstance(summary, dict)
        self.assertIn("status", summary)
        self.assertIn("backend", summary)
        self.assertIn("size_mb", summary)
        self.assertIn("memories", summary)
        self.assertIn("sessions", summary)
        self.assertIn("timestamp", summary)

    def test_get_db_summary_healthy(self):
        """get_db_summary status should be HEALTHY when healthy."""
        summary = self.monitor.get_db_summary()
        self.assertEqual(summary["status"], "HEALTHY")

    def test_get_report_has_thresholds(self):
        """get_report() should return full report with thresholds."""
        report = self.monitor.get_report()
        self.assertIn("thresholds", report)
        self.assertIn("total_snapshots", report)
        self.assertIn("total_alerts", report)

    def test_get_report_after_execute(self):
        """After execute(), get_report() should have snapshots."""
        self.monitor.execute()
        report = self.monitor.get_report()
        self.assertGreaterEqual(report["total_snapshots"], 0)

    def test_alert_on_warning_error_threshold(self):
        """Injecting WARN_ERRORS_7D errors should generate a warning alert."""
        threshold = self.monitor.WARN_ERRORS_7D
        # Seed enough errors to trigger the threshold
        extra = threshold + 5 - 3  # Already have 3 errors from setUp
        for i in range(extra):
            self.store.record_event(
                "StressTest", "error",
                {"index": i, "msg": "alert trigger error"},
                severity="error",
            )
        result = self.monitor.execute()
        self.assertGreater(result["total_alerts"], 0,
                           "Should have generated alert for high error count")

    def test_alert_on_critical_error_threshold(self):
        """Injecting CRITICAL_ERRORS_7D errors should generate a critical alert."""
        threshold = self.monitor.CRITICAL_ERRORS_7D
        extra = threshold + 10 - 3  # Already have 3 errors from setUp
        for i in range(extra):
            self.store.record_event(
                "StressTest", "critical_error",
                {"index": i, "msg": "critical alert"},
                severity="error",
            )
        result = self.monitor.execute()
        alerts = result.get("alerts", [])
        critical_alerts = [a for a in alerts if a["severity"] == "critical"]
        self.assertGreater(len(critical_alerts), 0,
                           "Should have generated critical alert for high error count")

    def test_monitor_start_stop(self):
        """Start and stop monitor should work cleanly."""
        self.assertFalse(self.monitor.is_monitor_running())
        self.monitor.start_monitor(interval_seconds=99999)
        self.assertTrue(self.monitor.is_monitor_running())
        self.monitor.stop_monitor()
        time.sleep(0.2)
        self.assertFalse(self.monitor.is_monitor_running())

    def test_start_twice_is_idempotent(self):
        """Starting the monitor twice should not create duplicate threads."""
        self.monitor.start_monitor(interval_seconds=99999)
        thread_id = id(self.monitor._monitor_thread)
        self.monitor.start_monitor(interval_seconds=99999)
        self.assertEqual(id(self.monitor._monitor_thread), thread_id)
        self.monitor.stop_monitor()

    def test_stop_before_start_is_safe(self):
        """Stopping before starting should not raise."""
        self.monitor.stop_monitor()
        self.assertFalse(self.monitor.is_monitor_running())

    def test_version_defined(self):
        """VERSION should be defined and non-empty."""
        self.assertTrue(hasattr(self.monitor, "VERSION"))
        self.assertGreater(len(self.monitor.VERSION), 0)

    def test_timestamp_is_iso_format(self):
        """Timestamp should be ISO format."""
        result = self.monitor.execute()
        ts = result["timestamp"]
        self.assertIn("T", ts)  # ISO format contains T

    def test_sources_count_matches_seeded(self):
        """sources_count should reflect the number of distinct sources."""
        result = self.monitor.execute()
        # We seeded with "TestSource" and "test_session" — at least 1 source
        self.assertGreaterEqual(result["sources_count"], 1)


class TestDatabaseMonitorWormThresholds(unittest.TestCase):
    """Test alert threshold configuration."""

    def setUp(self):
        self.store = _make_temp_store()
        _seed_store(self.store, errors=1, warnings=1, infos=10, sessions=0)
        from CORE.WORMS.expansion.db_monitor import DatabaseMonitorWorm
        self.monitor = DatabaseMonitorWorm(store=self.store)

    def tearDown(self):
        _destroy_temp_store(self.store)

    def test_thresholds_are_positive(self):
        """All threshold constants should be positive."""
        self.assertGreater(self.monitor.WARN_DB_SIZE_MB, 0)
        self.assertGreater(self.monitor.WARN_GROWTH_24H, 0)
        self.assertGreater(self.monitor.WARN_ERRORS_7D, 0)
        self.assertGreater(self.monitor.CRITICAL_DB_SIZE_MB, 0)
        self.assertGreater(self.monitor.CRITICAL_GROWTH_24H, 0)
        self.assertGreater(self.monitor.CRITICAL_ERRORS_7D, 0)

    def test_critical_higher_than_warn(self):
        """Critical thresholds should be higher than warning thresholds."""
        self.assertGreater(
            self.monitor.CRITICAL_DB_SIZE_MB, self.monitor.WARN_DB_SIZE_MB)
        self.assertGreater(
            self.monitor.CRITICAL_GROWTH_24H, self.monitor.WARN_GROWTH_24H)
        self.assertGreater(
            self.monitor.CRITICAL_ERRORS_7D, self.monitor.WARN_ERRORS_7D)


class TestDatabaseMonitorWormEmptyStore(unittest.TestCase):
    """Test monitor behavior with an empty store."""

    def setUp(self):
        self.store = _make_temp_store()  # No seed data
        from CORE.WORMS.expansion.db_monitor import DatabaseMonitorWorm
        self.monitor = DatabaseMonitorWorm(store=self.store)

    def tearDown(self):
        _destroy_temp_store(self.store)

    def test_empty_store_is_healthy(self):
        """An empty store should still be healthy (no alerts)."""
        result = self.monitor.execute()
        self.assertTrue(result["healthy"])
        self.assertEqual(result["total_memories"], 0)
        self.assertEqual(result["total_sessions"], 0)

    def test_empty_store_no_alerts(self):
        """An empty store should generate no alerts."""
        result = self.monitor.execute()
        self.assertEqual(result["total_alerts"], 0)

    def test_empty_store_growth_zero(self):
        """An empty store should show zero growth."""
        result = self.monitor.execute()
        self.assertEqual(result["memory_growth_24h"], 0)


class TestScheduledDbMonitorWorm(unittest.TestCase):
    """Unit tests for ScheduledDbMonitorWorm with store injection."""

    def setUp(self):
        self.store = _make_temp_store()
        _seed_store(self.store, errors=2, warnings=3, infos=30, sessions=1)
        from CORE.WORMS.expansion.scheduling import ScheduledDbMonitorWorm
        self.sched = ScheduledDbMonitorWorm(store=self.store)

    def tearDown(self):
        """Stop any running scheduler and clean up."""
        try:
            self.sched.stop_scheduler()
        except Exception:
            pass
        _destroy_temp_store(self.store)

    def test_execute_returns_run(self):
        """execute() should return a DbMonitorRun with expected attributes."""
        run = self.sched.execute()
        self.assertIsNotNone(run)
        self.assertTrue(hasattr(run, "run_id"))
        self.assertTrue(hasattr(run, "healthy"))
        self.assertTrue(hasattr(run, "db_size_mb"))
        self.assertTrue(hasattr(run, "total_memories"))
        self.assertTrue(hasattr(run, "total_sessions"))
        self.assertTrue(hasattr(run, "duration_ms"))
        self.assertTrue(hasattr(run, "success"))

    def test_execute_success(self):
        """execute() should succeed."""
        run = self.sched.execute()
        self.assertTrue(run.success)

    def test_execute_healthy(self):
        """Store should be reported as healthy."""
        run = self.sched.execute()
        self.assertTrue(run.healthy)

    def test_execute_reports_metrics(self):
        """execute() should report positive metrics."""
        run = self.sched.execute()
        self.assertGreater(run.total_memories, 30)
        self.assertGreater(run.db_size_mb, 0)

    def test_execute_has_unique_run_id(self):
        """run_id should be unique per execution."""
        run1 = self.sched.execute()
        run2 = self.sched.execute()
        self.assertNotEqual(run1.run_id, run2.run_id)

    def test_execute_has_timestamps(self):
        """started_at and completed_at should be valid."""
        run = self.sched.execute()
        self.assertGreater(len(run.started_at), 0)
        self.assertGreater(len(run.completed_at), 0)

    def test_get_status(self):
        """get_status() should return dict with expected keys."""
        status = self.sched.get_status()
        self.assertIsInstance(status, dict)
        self.assertIn("version", status)
        self.assertIn("scheduler_running", status)
        self.assertIn("interval_hours", status)
        self.assertIn("total_runs", status)
        self.assertIn("healthy_runs", status)
        self.assertIn("success_rate", status)

    def test_scheduler_start_stop(self):
        """Scheduler start and stop should work cleanly."""
        self.assertFalse(self.sched.get_status()["scheduler_running"])
        self.sched.start_scheduler(interval_hours=99999)
        self.assertTrue(self.sched.get_status()["scheduler_running"])
        self.sched.stop_scheduler()
        time.sleep(0.2)
        self.assertFalse(self.sched.get_status()["scheduler_running"])

    def test_start_twice_is_idempotent(self):
        """Starting scheduler twice should not create duplicate threads."""
        self.sched.start_scheduler(interval_hours=99999)
        thread_id = id(self.sched._scheduler_thread)
        self.sched.start_scheduler(interval_hours=99999)
        self.assertEqual(id(self.sched._scheduler_thread), thread_id)
        self.sched.stop_scheduler()

    def test_success_rate_after_execution(self):
        """After execution, success_rate should be 100%."""
        self.sched.execute()
        status = self.sched.get_status()
        self.assertEqual(status["success_rate"], 100.0)

    def test_success_rate_accumulates(self):
        """Multiple executions should accumulate in run history."""
        self.sched.execute()
        self.sched.execute()
        self.sched.execute()
        status = self.sched.get_status()
        self.assertEqual(status["total_runs"], 3)
        self.assertEqual(status["healthy_runs"], 3)
        self.assertEqual(status["success_rate"], 100.0)

    def test_version_defined(self):
        """VERSION should be defined."""
        self.assertTrue(hasattr(self.sched, "VERSION"))
        self.assertGreater(len(self.sched.VERSION), 0)

    def test_empty_store(self):
        """ScheduledDbMonitorWorm should handle empty stores gracefully."""
        empty_store = _make_temp_store()
        from CORE.WORMS.expansion.scheduling import ScheduledDbMonitorWorm
        sched = ScheduledDbMonitorWorm(store=empty_store)
        run = sched.execute()
        self.assertTrue(run.success)
        self.assertTrue(run.healthy)
        self.assertEqual(run.total_memories, 0)
        _destroy_temp_store(empty_store)


class TestDatabaseMonitorWormIntegrity(unittest.TestCase):
    """Test integrity check and backup integration."""

    def setUp(self):
        self.store = _make_temp_store()
        _seed_store(self.store, errors=2, warnings=3, infos=20, sessions=1)
        from CORE.WORMS.expansion.db_monitor import DatabaseMonitorWorm
        self.monitor = DatabaseMonitorWorm(store=self.store)

    def tearDown(self):
        _destroy_temp_store(self.store)

    def test_check_integrity_returns_dict(self):
        """check_integrity() should return a dict."""
        result = self.monitor.check_integrity()
        self.assertIsInstance(result, dict)
        self.assertIn("passed", result)
        self.assertIn("ok", result)
        self.assertIn("errors", result)

    def test_check_integrity_passes(self):
        """A healthy store should pass integrity check."""
        result = self.monitor.check_integrity()
        self.assertTrue(result["passed"])
        self.assertEqual(result["errors"], [])

    def test_check_integrity_reports_page_count(self):
        """Integrity check should report page details."""
        result = self.monitor.check_integrity()
        self.assertGreater(result.get("page_count", 0), 0)
        self.assertGreater(result.get("page_size", 0), 0)

    def test_create_backup_creates_file(self):
        """create_backup() should create a valid backup file."""
        import tempfile
        backup_dir = tempfile.mkdtemp()
        try:
            result = self.monitor.create_backup(target_dir=backup_dir)
            self.assertTrue(os.path.exists(result), f"Backup file not found: {result}")
            self.assertGreater(os.path.getsize(result), 0)
        finally:
            import shutil
            shutil.rmtree(backup_dir, ignore_errors=True)

    def test_create_backup_is_valid_sqlite(self):
        """Backup file should be a valid SQLite database with data."""
        import tempfile
        import sqlite3
        backup_dir = tempfile.mkdtemp()
        try:
            result = self.monitor.create_backup(target_dir=backup_dir)
            conn = sqlite3.connect(result)
            count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            conn.close()
            self.assertGreater(count, 20, f"Expected >20 memories, got {count}")
        finally:
            import shutil
            shutil.rmtree(backup_dir, ignore_errors=True)

    def test_health_check_with_integrity(self):
        """Health check should work correctly with integrity as a factor."""
        result = self.monitor.execute()
        self.assertIn("healthy", result)
        self.assertIn("db_size_mb", result)
        self.assertIn("total_memories", result)

    def test_backup_records_event_to_store(self):
        """create_backup() should record a backup event in the store."""
        import tempfile
        backup_dir = tempfile.mkdtemp()
        try:
            self.monitor.create_backup(target_dir=backup_dir)
            # Check that a backup event was recorded
            events = self.store.query_memories(source="DatabaseMonitorWorm",
                                                event_type="db_backup", limit=5)
            self.assertGreaterEqual(len(events), 1,
                                    "Backup event should be recorded to memory")
        finally:
            import shutil
            shutil.rmtree(backup_dir, ignore_errors=True)


# ── Direct runner ───────────────────────────────────────────────────
if __name__ == "__main__":
    p = f = 0
    for name, obj in sorted(globals().items()):
        if isinstance(obj, type) and issubclass(obj, unittest.TestCase) and name.startswith("Test"):
            suite = unittest.TestLoader().loadTestsFromTestCase(obj)
            runner = unittest.TextTestRunner(verbosity=2)
            result = runner.run(suite)
            p += result.testsRun - len(result.failures) - len(result.errors)
            f += len(result.failures) + len(result.errors)
    print(f"\n  Passed: {p}, Failed: {f}")
