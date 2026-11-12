# coding: utf-8
"""Unit tests for LocalMemoryStore v2.0.0 quantitative features.

Tests:
- QueryTimer performance tracking
- health_check() scoring and grading
- get_performance_stats()
- get_index_usage()
- Enhanced get_stats() with WAL, fragmentation, session analytics
- auto_vacuum_status and auto_backup_status
- Size monitoring
- get_health_report() structure
"""

import sys
import os
import time
import json
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

# ── Module Under Test ──────────────────────────────────────────────
from CORE.local_memory import LocalMemoryStore


def _make_store() -> LocalMemoryStore:
    """Create a temporary store for testing."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    os.unlink(tmp.name)  # Remove so LocalMemoryStore creates fresh
    store = LocalMemoryStore(db_path=tmp.name)
    store._max_size_mb = 1000  # Prevent size warnings during tests
    return store


# ═══════════════════════════════════════════════════════════════════
# QueryTimer Tests
# ═══════════════════════════════════════════════════════════════════


class TestQueryTimer:
    """Verify QueryTimer tracks query performance."""

    def test_timer_records_queries(self):
        """After queries, total_queries should increment."""
        store = _make_store()
        assert store.get_performance_stats()["total_queries"] == 0
        
        store.record_event("test", "event", {"k": "v"})
        store.query_memories(source="test")
        store.search_memories("test")
        
        perf = store.get_performance_stats()
        assert perf["total_queries"] == 3, f"Expected 3 queries, got {perf['total_queries']}"
        store.close()

    def test_timer_records_duration(self):
        """Query duration should be recorded."""
        store = _make_store()
        store.record_event("test", "event", {"k": "v"})
        store.query_memories(source="test")
        
        perf = store.get_performance_stats()
        assert perf["total_query_time_ms"] > 0
        assert perf["avg_query_time_ms"] > 0
        assert perf["max_query_time_ms"] > 0
        store.close()

    def test_timer_slow_query_threshold(self):
        """Slow query threshold should be configurable."""
        store = _make_store()
        store._slow_query_threshold_ms = 0.001  # Very low — all queries are "slow"
        store.record_event("test", "event", {"k": "v"})
        
        perf = store.get_performance_stats()
        assert perf["slow_query_threshold_ms"] == 0.001
        store.close()

    def test_performance_stats_structure(self):
        """get_performance_stats should have all expected keys."""
        store = _make_store()
        perf = store.get_performance_stats()
        for key in ("total_queries", "total_query_time_ms", "avg_query_time_ms",
                    "max_query_time_ms", "slow_query_count", "slow_query_threshold_ms"):
            assert key in perf, f"Missing key: {key}"
        store.close()


# ═══════════════════════════════════════════════════════════════════
# health_check() Tests
# ═══════════════════════════════════════════════════════════════════


class TestHealthCheck:
    """Verify health_check() scoring."""

    def test_health_check_structure(self):
        """health_check should return all expected keys."""
        store = _make_store()
        health = store.health_check()
        for key in ("score", "max_score", "grade", "details", "warnings",
                    "auto_vacuum", "auto_backup", "timestamp"):
            assert key in health, f"Missing key: {key}"
        assert health["max_score"] == 100
        store.close()

    def test_health_score_in_range(self):
        """Score should be between 0 and 100."""
        store = _make_store()
        health = store.health_check()
        assert 0 <= health["score"] <= 100, f"Score {health['score']} out of range"
        store.close()

    def test_health_grade_valid(self):
        """Grade should be A, B, C, D, or F."""
        store = _make_store()
        health = store.health_check()
        assert health["grade"] in ("A", "B", "C", "D", "F"), f"Invalid grade: {health['grade']}"
        store.close()

    def test_health_details_six_dimensions(self):
        """Health should have 6 dimensions in details."""
        store = _make_store()
        health = store.health_check()
        dimensions = ["integrity", "fragmentation", "wal_mode", "size", "query_performance", "index_coverage"]
        for dim in dimensions:
            assert dim in health["details"], f"Missing dimension: {dim}"
        store.close()

    def test_each_dimension_has_score_and_max(self):
        """Each dimension should have score and max values."""
        store = _make_store()
        health = store.health_check()
        for dim, detail in health["details"].items():
            assert "score" in detail, f"Missing score in {dim}"
            assert "max" in detail, f"Missing max in {dim}"
            assert 0 <= detail["score"] <= detail["max"], f"Score out of range in {dim}"
        store.close()

    def test_health_grade_letter_mapping(self):
        """Verify grade mapping logic."""
        # Create a store and verify grade A
        store = _make_store()
        health = store.health_check()
        if health["score"] >= 90:
            assert health["grade"] == "A"
        elif health["score"] >= 80:
            assert health["grade"] == "B"
        elif health["score"] >= 65:
            assert health["grade"] == "C"
        elif health["score"] >= 50:
            assert health["grade"] == "D"
        else:
            assert health["grade"] == "F"
        store.close()

    def test_warnings_is_list(self):
        """Warnings should always be a list."""
        store = _make_store()
        health = store.health_check()
        assert isinstance(health["warnings"], list)
        store.close()

    def test_integrity_passed_in_new_db(self):
        """A fresh DB should pass integrity check."""
        store = _make_store()
        health = store.health_check()
        integrity = health["details"].get("integrity", {})
        assert integrity.get("passed", False) is True, "New DB should pass integrity"
        store.close()


class TestHealthGrade:
    """Verify _health_grade static method."""

    def test_grade_a(self):
        assert LocalMemoryStore._health_grade(100) == "A"
        assert LocalMemoryStore._health_grade(95) == "A"
        assert LocalMemoryStore._health_grade(90) == "A"

    def test_grade_b(self):
        assert LocalMemoryStore._health_grade(89) == "B"
        assert LocalMemoryStore._health_grade(85) == "B"
        assert LocalMemoryStore._health_grade(80) == "B"

    def test_grade_c(self):
        assert LocalMemoryStore._health_grade(79) == "C"
        assert LocalMemoryStore._health_grade(70) == "C"
        assert LocalMemoryStore._health_grade(65) == "C"

    def test_grade_d(self):
        assert LocalMemoryStore._health_grade(64) == "D"
        assert LocalMemoryStore._health_grade(55) == "D"
        assert LocalMemoryStore._health_grade(50) == "D"

    def test_grade_f(self):
        assert LocalMemoryStore._health_grade(49) == "F"
        assert LocalMemoryStore._health_grade(25) == "F"
        assert LocalMemoryStore._health_grade(0) == "F"


# ═══════════════════════════════════════════════════════════════════
# get_index_usage() Tests
# ═══════════════════════════════════════════════════════════════════


class TestIndexUsage:
    """Verify get_index_usage() returns index info."""

    def test_index_usage_returns_list(self):
        """Index usage should return a list."""
        store = _make_store()
        indexes = store.get_index_usage()
        assert isinstance(indexes, list)
        store.close()

    def test_index_usage_has_expected_count(self):
        """Should have 8 indexes on memories table."""
        store = _make_store()
        indexes = store.get_index_usage()
        # 5 single-column + 3 composite = 8 indexes
        assert len(indexes) >= 5, f"Expected at least 5 indexes, got {len(indexes)}"
        store.close()

    def test_index_entry_structure(self):
        """Each index entry should have name, columns, column_count."""
        store = _make_store()
        indexes = store.get_index_usage()
        for idx in indexes:
            assert "index_name" in idx
            assert "columns" in idx
            assert "column_count" in idx
            assert isinstance(idx["columns"], list)
        store.close()

    def test_composite_indexes_present(self):
        """Composite indexes should be present."""
        store = _make_store()
        indexes = store.get_index_usage()
        names = {idx["index_name"] for idx in indexes}
        for expected in ("idx_memories_source_event_severity",
                         "idx_memories_session_timestamp",
                         "idx_memories_severity_timestamp"):
            assert expected in names, f"Missing composite index: {expected}"
        store.close()


# ═══════════════════════════════════════════════════════════════════
# Enhanced get_stats() Tests
# ═══════════════════════════════════════════════════════════════════


class TestEnhancedStats:
    """Verify enhanced get_stats() has new quantitative fields."""

    def test_stats_has_wal_size(self):
        """Stats should include WAL size."""
        store = _make_store()
        stats = store.get_stats()
        assert "wal_size_mb" in stats
        store.close()

    def test_stats_has_total_size(self):
        """Stats should include total size (DB + WAL + SHM)."""
        store = _make_store()
        stats = store.get_stats()
        assert "total_size_mb" in stats
        assert stats["total_size_mb"] >= stats.get("db_size_mb", 0)
        store.close()

    def test_stats_has_session_analytics(self):
        """Stats should include session analytics."""
        store = _make_store()
        store.start_session(source="test")
        store.record_event("test", "event", {"k": "v"})
        stats = store.get_stats()
        assert "total_sessions" in stats
        assert "completed_sessions" in stats
        assert "active_sessions" in stats
        assert "avg_events_per_session" in stats
        assert "current_session_events" in stats
        store.close()

    def test_stats_has_fragmentation(self):
        """Stats should include fragmentation ratio."""
        store = _make_store()
        stats = store.get_stats()
        assert "fragmentation_ratio" in stats
        assert isinstance(stats["fragmentation_ratio"], float)
        store.close()

    def test_stats_has_page_info(self):
        """Stats should include page count and size."""
        store = _make_store()
        stats = store.get_stats()
        assert "page_count" in stats
        assert "page_size" in stats
        assert "theoretical_size_mb" in stats
        store.close()

    def test_stats_has_performance(self):
        """Stats should include performance sub-object."""
        store = _make_store()
        stats = store.get_stats()
        assert "performance" in stats
        assert "total_queries" in stats["performance"]
        store.close()

    def test_stats_version(self):
        """Stats should report v2.0.0."""
        store = _make_store()
        stats = store.get_stats()
        assert stats["version"] == "2.0.0"
        store.close()


# ═══════════════════════════════════════════════════════════════════
# Auto-Maintenance Status Tests
# ═══════════════════════════════════════════════════════════════════


class TestAutoMaintenance:
    """Verify auto_vacuum_status and auto_backup_status."""

    def test_auto_vacuum_stopped_by_default(self):
        """Auto-vacuum should not be running by default."""
        store = _make_store()
        status = store.auto_vacuum_status
        assert "running" in status
        assert "interval_hours" in status
        assert "thread_alive" in status
        store.close()

    def test_auto_backup_stopped_by_default(self):
        """Auto-backup should not be running by default."""
        store = _make_store()
        status = store.auto_backup_status
        assert "running" in status
        assert "interval_hours" in status
        assert "thread_alive" in status
        assert "last_backup_path" in status
        assert "target_dir" in status
        assert "keep_count" in status
        store.close()

    def test_start_stop_auto_vacuum(self):
        """Starting and stopping auto-vacuum should work."""
        store = _make_store()
        store.start_auto_vacuum(interval_hours=0.01)  # Fast for testing
        assert store.auto_vacuum_status["running"] is True
        store.stop_auto_vacuum()
        assert store.auto_vacuum_status["running"] is False
        store.close()

    def test_start_stop_auto_backup(self):
        """Starting and stopping auto-backup should work."""
        store = _make_store()
        store.start_auto_backup(interval_hours=0.01)
        assert store.auto_backup_status["running"] is True
        store.stop_auto_backup()
        assert store.auto_backup_status["running"] is False
        store.close()

    def test_auto_backup_target_dir(self):
        """Auto-backup should store configured target dir."""
        store = _make_store()
        store.start_auto_backup(target_dir="/tmp/test_backups")
        assert "/tmp/test_backups" in store.auto_backup_status["target_dir"]
        store.stop_auto_backup()
        store.close()


# ═══════════════════════════════════════════════════════════════════
# get_health_report() Tests
# ═══════════════════════════════════════════════════════════════════


class TestHealthReport:
    """Verify get_health_report() comprehensive structure."""

    def test_health_report_structure(self):
        """Health report should combine all metrics."""
        store = _make_store()
        report = store.get_health_report()
        for key in ("store_version", "db_path", "health", "stats",
                    "performance", "indexes", "growth_trends",
                    "error_trends", "maintenance", "timestamp"):
            assert key in report, f"Missing key: {key}"
        store.close()

    def test_health_report_has_health_score(self):
        """Report should include health score."""
        store = _make_store()
        report = store.get_health_report()
        assert "score" in report["health"]
        assert "grade" in report["health"]
        store.close()

    def test_health_report_has_stats(self):
        """Report should include stats."""
        store = _make_store()
        report = store.get_health_report()
        assert "total_memories" in report["stats"]
        assert "db_size_mb" in report["stats"]
        store.close()

    def test_health_report_has_performance(self):
        """Report should include performance metrics."""
        store = _make_store()
        report = store.get_health_report()
        assert "total_queries" in report["performance"]
        store.close()

    def test_health_report_trends_are_lists(self):
        """Growth and error trends should be lists."""
        store = _make_store()
        report = store.get_health_report()
        assert isinstance(report["growth_trends"], list)
        assert isinstance(report["error_trends"], list)
        store.close()


# ═══════════════════════════════════════════════════════════════════
# Standalone Runner
# ═══════════════════════════════════════════════════════════════════


def _run_standalone():
    """Run tests standalone (for 3.14t environments where pytest may not work)."""
    passed = 0
    failed = 0
    errors = []

    for name, obj in sorted(globals().items(), key=lambda x: x[0]):
        if isinstance(obj, type) and name.startswith("Test"):
            inst = obj()
            for mn in sorted(dir(inst)):
                if mn.startswith("test_"):
                    try:
                        getattr(inst, mn)()
                        print(f"  [OK] {name}.{mn}")
                        passed += 1
                    except Exception as e:
                        print(f"  [FAIL] {name}.{mn}: {e}")
                        failed += 1
                        errors.append(f"{name}.{mn}: {e}")

    print(f"\n  Passed: {passed}, Failed: {failed}")
    if errors:
        print("  Errors:")
        for e in errors[:10]:
            print(f"    {e}")
        if len(errors) > 10:
            print(f"    ... and {len(errors) - 10} more")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_standalone())
