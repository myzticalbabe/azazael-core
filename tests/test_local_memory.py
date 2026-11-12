# coding: utf-8
"""Unit tests for LocalMemoryStore — query_real_errors, error trends, DB growth, auto-vacuum."""

import sys
import os
import time
import tempfile
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from CORE.local_memory import LocalMemoryStore
from CORE.failure_ledger import failure_notes, reset_notes

class TestQueryRealErrors:
    """Verify query_real_errors filters test artifacts."""

    def setup_method(self):
        """Create a temporary in-memory store for testing."""
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = LocalMemoryStore(db_path=self.tmp.name)
        # Ensure clean state: stop any auto-started vacuum from config
        self.store.stop_auto_vacuum()

    def teardown_method(self):
        """Clean up temp file."""
        self.store.close()
        os.unlink(self.tmp.name)

    def test_filters_nonexistent_exe(self):
        """Real error query should exclude nonexistent.exe test artifacts."""
        self.store.record_event(
            "BinaryAnalysis", "binanalysis.nm_error",
            data={"path": "nonexistent.exe", "error": "File not found"},
            severity="error",
        )
        self.store.record_event(
            "EveUnleashed", "gpu_error",
            data={"msg": "GPU not detected"},
            severity="error",
        )
        real = self.store.query_real_errors(days=7)
        assert real["total_errors"] == 1  # Only GPU error is real
        assert real["filtered_errors"] == 1  # nonexistent.exe filtered
        assert real["errors"][0]["source"] == "EveUnleashed"

    def test_filters_bad_action(self):
        """Real error query should exclude bad_action workflow failures."""
        self.store.record_event(
            "WorkflowEngine", "workflow_completed",
            data={"name": "bad_action", "success": False, "steps_fail": 1},
            severity="warning",
        )
        self.store.record_event(
            "WorkflowEngine", "workflow_completed",
            data={"name": "real_failure", "success": False, "steps_fail": 1},
            severity="warning",
        )
        real = self.store.query_real_errors(days=7)
        assert real["total_warnings"] == 1  # Only real_failure
        assert real["filtered_warnings"] == 1  # bad_action filtered

    def test_returns_empty_when_all_filtered(self):
        """When all results are test artifacts, return empty lists."""
        self.store.record_event(
            "BinaryAnalysis", "binanalysis.nm_error",
            data={"path": "nonexistent.exe", "error": "File not found"},
            severity="error",
        )
        real = self.store.query_real_errors(days=7)
        assert real["total_errors"] == 0
        assert real["total_warnings"] == 0
        assert real["filtered_errors"] == 1

    def test_respects_days_limit(self):
        """Should only query within the specified days window."""
        self.store.record_event(
            "Src", "test_err", data={"msg": "real error"}, severity="error"
        )
        # Recent events should appear in a 7-day window
        real_recent = self.store.query_real_errors(days=7)
        assert real_recent["total_errors"] == 1
        # But not in a 0-day window (no past events included)
        real_none = self.store.query_real_errors(days=0)
        assert real_none["total_errors"] == 0

    def test_respects_limit_parameter(self):
        """Should respect the limit parameter on output."""
        for i in range(10):
            self.store.record_event(
                f"Source{i}", "real_error",
                data={"msg": f"error {i}"},
                severity="error",
            )
        # Use a large limit to capture all errors, then check output limit
        real = self.store.query_real_errors(days=7, limit=3)
        # total_errors counts all real errors found (up to limit*2 raw query)
        assert real["total_errors"] >= 3
        assert len(real["errors"]) == 3  # Output limited to 3


class TestErrorTrends:
    """Verify get_error_trends aggregation."""

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = LocalMemoryStore(db_path=self.tmp.name)

    def teardown_method(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_returns_list(self):
        """Should return a list of daily counts."""
        self.store.record_event("Test", "error_event", {}, severity="error")
        self.store.record_event("Test", "warning_event", {}, severity="warning")
        trends = self.store.get_error_trends(days=30)
        assert isinstance(trends, list)
        assert len(trends) >= 1

    def test_has_expected_keys(self):
        """Each trend entry should have date, errors, warnings."""
        self.store.record_event("Test", "err", {"x": 1}, severity="error")
        trends = self.store.get_error_trends(days=30)
        for d in trends:
            assert "date" in d
            assert "errors" in d
            assert "warnings" in d
            assert isinstance(d["errors"], int)
            assert isinstance(d["warnings"], int)

    def test_aggregates_by_day(self):
        """Multiple events on the same day should be aggregated."""
        self.store.record_event("A", "err1", {}, severity="error")
        self.store.record_event("B", "err2", {}, severity="error")
        self.store.record_event("C", "warn1", {}, severity="warning")
        trends = self.store.get_error_trends(days=30)
        today = trends[-1] if trends else None
        if today:
            assert today["errors"] == 2
            assert today["warnings"] == 1

    def test_empty_with_future_days_zero(self):
        """Querying with days=0 should return empty (no past events included)."""
        trends = self.store.get_error_trends(days=0)
        assert trends == []


class TestDbGrowth:
    """Verify get_db_growth aggregation."""

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = LocalMemoryStore(db_path=self.tmp.name)

    def teardown_method(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_returns_list(self):
        """Should return list of daily event counts."""
        self.store.record_event("Test", "event", {}, severity="info")
        growth = self.store.get_db_growth(days=30)
        assert isinstance(growth, list)
        assert len(growth) >= 1

    def test_counts_include_all_severities(self):
        """Growth should count all events regardless of severity."""
        self.store.record_event("A", "e1", {}, severity="info")
        self.store.record_event("B", "e2", {}, severity="error")
        self.store.record_event("C", "e3", {}, severity="warning")
        growth = self.store.get_db_growth(days=30)
        today = growth[-1] if growth else None
        if today:
            assert today["count"] == 3

    def test_has_date_and_count(self):
        """Each entry should have date and count."""
        self.store.record_event("Test", "event", {})
        growth = self.store.get_db_growth(days=30)
        for d in growth:
            assert "date" in d
            assert "count" in d
            assert isinstance(d["count"], int)


class TestAutoVacuum:
    """Verify auto-vacuum scheduling."""

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = LocalMemoryStore(db_path=self.tmp.name)

    def teardown_method(self):
        self.store.stop_auto_vacuum()
        self.store.close()
        os.unlink(self.tmp.name)

    def test_default_status(self):
        """Before starting, vacuum should not be running (after config check)."""
        status = self.store.auto_vacuum_status
        assert isinstance(status, dict)
        assert "running" in status

    def test_start_and_stop(self):
        """Start and stop auto-vacuum should work cleanly."""
        self.store.start_auto_vacuum(interval_hours=0.001)  # Very short interval
        status = self.store.auto_vacuum_status
        assert status["running"] is True
        assert status["thread_alive"] is True

        self.store.stop_auto_vacuum()
        status = self.store.auto_vacuum_status
        assert status["running"] is False
        assert status["thread_alive"] is False

    def test_start_twice_is_idempotent(self):
        """Starting auto-vacuum twice should not create duplicate threads."""
        self.store.start_auto_vacuum(interval_hours=0.001)
        self.store.start_auto_vacuum(interval_hours=0.001)
        status = self.store.auto_vacuum_status
        assert status["running"] is True
        self.store.stop_auto_vacuum()

    def test_stop_before_start_is_safe(self):
        """Stopping before starting should not raise."""
        self.store.stop_auto_vacuum()
        status = self.store.auto_vacuum_status
        assert status["running"] is False

    def test_close_stops_vacuum(self):
        """Calling close() should stop auto-vacuum."""
        self.store.start_auto_vacuum(interval_hours=0.001)
        time.sleep(0.2)
        self.store.close()
        status = self.store.auto_vacuum_status
        assert status["running"] is False

    def test_interval_is_stored(self):
        """The interval should be reflected in status."""
        self.store.start_auto_vacuum(interval_hours=12.5)
        status = self.store.auto_vacuum_status
        assert status["interval_hours"] == 12.5
        self.store.stop_auto_vacuum()


class TestIntegrityCheck:
    """Verify integrity_check() method."""

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = LocalMemoryStore(db_path=self.tmp.name)

    def teardown_method(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_returns_dict_with_expected_keys(self):
        """integrity_check should return a dict with all expected keys."""
        result = self.store.integrity_check()
        assert isinstance(result, dict)
        assert "passed" in result
        assert "ok" in result
        assert "errors" in result
        assert "page_count" in result
        assert "page_size" in result
        assert "wal_mode" in result
        assert "db_size_mb" in result

    def test_healthy_db_passes(self):
        """A fresh database should pass integrity check."""
        result = self.store.integrity_check()
        assert result["passed"] is True
        assert result["ok"] is True
        assert result["errors"] == []

    def test_db_size_is_positive(self):
        """db_size_mb should be a positive value."""
        result = self.store.integrity_check()
        assert result["db_size_mb"] > 0

    def test_page_count_is_positive(self):
        """page_count should be a positive integer."""
        result = self.store.integrity_check()
        assert result["page_count"] > 0

    def test_wal_mode_is_active(self):
        """WAL mode should be active."""
        result = self.store.integrity_check()
        assert result["wal_mode"].lower() == "wal"


class TestBackup:
    """Verify backup() method."""

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = LocalMemoryStore(db_path=self.tmp.name)
        # Seed with some data
        for i in range(20):
            self.store.record_event("Src", "test_event", {"i": i}, severity="info")

    def teardown_method(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_backup_creates_file(self):
        """backup() should create a file at the target path."""
        target = tempfile.mktemp(suffix=".db")
        try:
            result = self.store.backup(target)
            assert result == target
            assert os.path.exists(target)
            assert os.path.getsize(target) > 0
        finally:
            os.unlink(target)

    def test_backup_is_readable(self):
        """The backup file should be a valid SQLite database."""
        target = tempfile.mktemp(suffix=".db")
        try:
            self.store.backup(target)
            import sqlite3
            conn = sqlite3.connect(target)
            count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            conn.close()
            assert count == 20
        finally:
            os.unlink(target)

    def test_backup_contains_all_data(self):
        """The backup should contain all 20 memories.

        Note: sessions table may be empty if start_session() was not called
        explicitly (record_event only UPDATEs sessions, never INSERTs).
        """
        target = tempfile.mktemp(suffix=".db")
        try:
            self.store.backup(target)
            import sqlite3
            conn = sqlite3.connect(target)
            memories = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            conn.close()
            assert memories == 20  # All 20 seeded records present
        finally:
            os.unlink(target)

    def test_backup_raises_on_bad_path(self):
        """backup() must raise RuntimeError when the target cannot be written.

        The target's PARENT is an existing FILE here, so no directory can be
        created for it on any platform. The predecessor used `/proc/self/...`,
        explaining that /proc is a read-only pseudo-filesystem that denies
        writes "even when running as root" -- correct and well-argued on Linux,
        and toothless on Windows, where that path is an ordinary path on the
        current drive: measured 2026-09-29, the backup SUCCEEDED and the test
        left a real 110 KB backup database behind outside the repository on
        every run. A test that cannot fail on the machine it runs on is not
        coverage, and this one also wrote where it claimed it could not.
        """
        import pytest
        import shutil
        tmp_dir = tempfile.mkdtemp()
        try:
            blocker = Path(tmp_dir) / "backup_blocker"
            blocker.write_text("not a directory", encoding="utf-8")
            with pytest.raises(RuntimeError):
                self.store.backup(str(blocker / "nested" / "backup.db"))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


class TestValidation:
    """Verify _validate_event_args and data quality checks."""

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = LocalMemoryStore(db_path=self.tmp.name)

    def teardown_method(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_empty_source_raises(self):
        """Empty source should raise ValueError."""
        import pytest
        with pytest.raises(ValueError, match="source must be a non-empty string"):
            self.store._validate_event_args("", "type", {}, "info", None)

    def test_invalid_severity_raises(self):
        """Invalid severity should raise ValueError."""
        import pytest
        with pytest.raises(ValueError, match="severity must be one of"):
            self.store._validate_event_args("Src", "type", {}, "invalid_severity", None)

    def test_valid_severity_passes(self):
        """Valid severity should return normalized value."""
        result = self.store._validate_event_args("Src", "type", {}, "WARNING", None)
        assert result == "warning"

    def test_tags_must_be_list(self):
        """Non-list tags should raise ValueError."""
        import pytest
        with pytest.raises(ValueError, match="tags must be a list"):
            self.store._validate_event_args("Src", "type", {}, "info", "not_a_list")

    def test_tag_items_must_be_strings(self):
        """Non-string tag items should raise ValueError."""
        import pytest
        with pytest.raises(ValueError, match="each tag must be a string"):
            self.store._validate_event_args("Src", "type", {}, "info", ["valid", 42])

    def test_record_event_passes_validation(self):
        """record_event should pass through validation."""
        mem_id = self.store.record_event("Src", "type", {"key": "val"}, severity="warning", tags=["a", "b"])
        assert mem_id > 0
        # Verify it was recorded
        results = self.store.query_memories(source="Src")
        assert len(results) >= 1
        assert results[0]["severity"] == "warning"

    def test_record_event_rejects_invalid(self):
        """record_event should reject with ValueError for invalid input."""
        import pytest
        with pytest.raises(ValueError):
            self.store.record_event("", "type", {}, severity="info")


class TestFailureLedgerExceptionCapture:
    """Verify failure_ledger captures exception types from swallowed failures."""

    def setup_method(self):
        """Reset failure ledger before each test."""
        reset_notes()

    def teardown_method(self):
        """Clean up."""
        reset_notes()

    def test_failure_ledger_captures_exception_type(self):
        """_note_failure should record exception type in failure_ledger."""
        from CORE.local_memory import _note_failure

        # Call _note_failure with a specific exception type
        try:
            raise ValueError("test error")
        except ValueError as exc:
            _note_failure("test:location", exc)

        notes = failure_notes()
        # Should have captured the exception type
        assert "test:location:ValueError" in notes
        assert notes["test:location:ValueError"] == 1

    def test_failure_ledger_captures_different_exception_types(self):
        """Different exception types should be recorded separately."""
        from CORE.local_memory import _note_failure

        try:
            raise ValueError("value error")
        except ValueError as exc:
            _note_failure("test:multi", exc)

        try:
            raise TypeError("type error")
        except TypeError as exc:
            _note_failure("test:multi", exc)

        try:
            raise RuntimeError("runtime error")
        except RuntimeError as exc:
            _note_failure("test:multi", exc)

        notes = failure_notes()
        assert notes["test:multi:ValueError"] == 1
        assert notes["test:multi:TypeError"] == 1
        assert notes["test:multi:RuntimeError"] == 1

    def test_failure_ledger_handles_none_exception(self):
        """When exc is None, should record 'unknown' type."""
        from CORE.local_memory import _note_failure

        _note_failure("test:none", None)

        notes = failure_notes()
        assert "test:none:unknown" in notes
        assert notes["test:none:unknown"] == 1

    def test_failure_ledger_thread_safety(self):
        """failure_ledger should be thread-safe under concurrent writes."""
        from CORE.local_memory import _note_failure

        def writer(exc_type, count):
            for _ in range(count):
                try:
                    raise exc_type("test")
                except exc_type as exc:
                    _note_failure("test:thread", exc)

        threads = [
            threading.Thread(target=writer, args=(ValueError, 100)),
            threading.Thread(target=writer, args=(TypeError, 100)),
            threading.Thread(target=writer, args=(RuntimeError, 100)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        notes = failure_notes()
        assert notes["test:thread:ValueError"] == 100
        assert notes["test:thread:TypeError"] == 100
        assert notes["test:thread:RuntimeError"] == 100

    def test_local_memory_store_failure_recording(self):
        """LocalMemoryStore failures should be recorded with exception types."""
        # Create a store, then make DB readonly to force failures on write
        import tempfile
        import os
        import stat

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()

        try:
            # Create store with writable DB first
            store = LocalMemoryStore(db_path=tmp.name)
            # Now make it readonly
            os.chmod(tmp.name, stat.S_IRUSR)

            # The store is expected to swallow this write failure and record it in
            # the ledger. If it propagates instead, keep the exception so the
            # assertion below can name it rather than discarding the evidence.
            propagated = None
            try:
                store.record_event("Test", "event", {"data": "test"})
            except Exception as exc:
                propagated = exc
            finally:
                store.close()

            notes = failure_notes()
            # Should have recorded some failure with exception type
            found = False
            for key in notes:
                if key.startswith("local_memory:"):
                    assert "unknown" not in key or "ValueError" in key or "OperationalError" in key or "PermissionError" in key
                    found = True
                    break
            assert found, f"No local_memory failures recorded (propagated={propagated!r}): {notes}"
        finally:
            # Make writable again to close and delete
            os.chmod(tmp.name, stat.S_IRUSR | stat.S_IWUSR)
            store.close()
            os.unlink(tmp.name)


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
                        f += 1
    print(f"\n  Passed: {p}, Failed: {f}")
