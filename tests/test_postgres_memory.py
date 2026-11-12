# coding: utf-8
"""
Unit tests for PostgresMemoryStore — PostgreSQL-backed memory store.

These tests gracefully skip if:
- psycopg2 is not installed
- PostgreSQL is not running / accessible via AZAZAEL_PG_DSN

Run with:
    python -m pytest tests/test_postgres_memory.py -v
    python tests/test_postgres_memory.py  # Direct runner
"""

import sys
import os
import json
import unittest
import tempfile
from datetime import datetime
from pathlib import Path

# Ensure src/ is on path
_src_dir = Path(__file__).resolve().parent.parent / "src"
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

# ── Check prerequisites ─────────────────────────────────────────────
try:
    import psycopg2
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

PG_DSN = os.environ.get("AZAZAEL_PG_DSN", os.environ.get("DATABASE_URL", ""))
PG_AVAILABLE = False

if HAS_PSYCOPG2 and PG_DSN:
    try:
        conn = psycopg2.connect(PG_DSN)
        conn.close()
        PG_AVAILABLE = True
    except Exception:
        PG_AVAILABLE = False


def skip_if_no_pg():
    """Skip decorator for PostgreSQL-dependent tests."""
    if not HAS_PSYCOPG2:
        raise unittest.SkipTest("psycopg2 not installed. Install with: pip install psycopg2-binary")
    if not PG_AVAILABLE:
        raise unittest.SkipTest(
            "PostgreSQL not available. Set AZAZAEL_PG_DSN or DATABASE_URL env var."
        )


class TestPostgresMemoryStore(unittest.TestCase):
    """Integration tests for PostgresMemoryStore."""

    @classmethod
    def setUpClass(cls):
        skip_if_no_pg()

    def setUp(self):
        from CORE.postgres_memory import PostgresMemoryStore
        # Use a unique session suffix to isolate test data
        self.store = PostgresMemoryStore(dsn=PG_DSN)
        self.test_session = f"test_session_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

    def tearDown(self):
        # Clean up test memories
        try:
            conn = psycopg2.connect(PG_DSN)
            with conn.cursor() as cur:
                cur.execute("DELETE FROM memories WHERE source = 'PostgresMemoryTest'")
                cur.execute("DELETE FROM sessions WHERE session_id LIKE 'test_session_%'")
            conn.commit()
            conn.close()
        except Exception:
            pass
        self.store.close()

    def test_record_event(self):
        """Record an event and verify it returns a valid ID."""
        mem_id = self.store.record_event(
            source="PostgresMemoryTest",
            event_type="test_record",
            data={"test": True, "value": 42},
            severity="info",
            tags=["test", "unit"],
            session_id=self.test_session,
        )
        self.assertIsInstance(mem_id, int)
        self.assertGreater(mem_id, 0)

    def test_query_by_source(self):
        """Query events filtered by source."""
        self.store.record_event("PostgresMemoryTest", "test_query",
                                {"msg": "hello"}, session_id=self.test_session)
        self.store.record_event("OtherSource", "other_query",
                                {"msg": "world"}, session_id=self.test_session)
        results = self.store.query_memories(source="PostgresMemoryTest", limit=10)
        self.assertGreaterEqual(len(results), 1)
        for r in results:
            self.assertEqual(r["source"], "PostgresMemoryTest")

    def test_search_fulltext(self):
        """Full-text search should find matching records."""
        self.store.record_event("PostgresMemoryTest", "test_search_fts",
                                {"description": "unique_search_term_xyz"},
                                session_id=self.test_session)
        results = self.store.search_memories("unique_search_term_xyz", limit=10)
        self.assertGreaterEqual(len(results), 1)
        found = any("unique_search_term_xyz" in str(r.get("data", {}))
                    for r in results)
        self.assertTrue(found, "Full-text search should find the record")

    def test_recall_recent(self):
        """Recent recall should return newest records."""
        self.store.record_event("PostgresMemoryTest", "test_recall",
                                {}, session_id=self.test_session)
        recent = self.store.recall_recent(limit=5, source="PostgresMemoryTest")
        self.assertGreaterEqual(len(recent), 1)

    def test_session_lifecycle(self):
        """Session start → record → end → list should work."""
        sid = self.store.start_session(
            source="PostgresMemoryTest",
            metadata={"test": "session_lifecycle"},
        )
        self.assertIsInstance(sid, str)
        self.assertGreater(len(sid), 0)

        self.store.record_event("PostgresMemoryTest", "test_session_event",
                                {}, session_id=sid)
        self.store.end_session(sid)

        sessions = self.store.list_sessions(limit=10)
        session_ids = [s["session_id"] for s in sessions]
        self.assertIn(sid, session_ids)

    def test_get_session(self):
        """Get session should return session info with memories."""
        sid = self.store.start_session(
            source="PostgresMemoryTest",
            metadata={"test": "get_session"},
        )
        self.store.record_event("PostgresMemoryTest", "session_event_1",
                                {"step": 1}, session_id=sid)
        self.store.record_event("PostgresMemoryTest", "session_event_2",
                                {"step": 2}, session_id=sid)

        session = self.store.get_session(sid)
        self.assertEqual(session["session_id"], sid)
        self.assertGreaterEqual(len(session.get("memories", [])), 2)

    def test_get_stats(self):
        """Stats should return valid metrics."""
        self.store.record_event("PostgresMemoryTest", "test_stats",
                                {"val": 1}, session_id=self.test_session)
        stats = self.store.get_stats()
        self.assertIn("total_memories", stats)
        self.assertIn("total_sessions", stats)
        self.assertIn("db_size_mb", stats)
        self.assertIn("version", stats)

    def test_error_trends(self):
        """Error trends should return daily aggregation."""
        self.store.record_event("PostgresMemoryTest", "test_err",
                                {}, severity="error", session_id=self.test_session)
        self.store.record_event("PostgresMemoryTest", "test_warn",
                                {}, severity="warning", session_id=self.test_session)
        trends = self.store.get_error_trends(days=30)
        self.assertIsInstance(trends, list)
        if trends:
            self.assertIn("date", trends[0])
            self.assertIn("errors", trends[0])
            self.assertIn("warnings", trends[0])

    def test_db_growth(self):
        """DB growth should return daily event counts."""
        self.store.record_event("PostgresMemoryTest", "test_growth",
                                {}, session_id=self.test_session)
        growth = self.store.get_db_growth(days=30)
        self.assertIsInstance(growth, list)
        if growth:
            self.assertIn("date", growth[0])
            self.assertIn("count", growth[0])

    def test_purge_old(self):
        """Purge old should remove old records."""
        # Record events with very old timestamps via direct SQL
        conn = psycopg2.connect(PG_DSN)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO memories(session_id, source, event_type, data, severity, tags, timestamp) "
                "VALUES(%s, %s, %s, %s, %s, %s, NOW() - INTERVAL '365 days')",
                (self.test_session, "PostgresMemoryTest", "old_event",
                 '{}', 'info', '[]')
            )
        conn.commit()
        conn.close()

        deleted = self.store.purge_old(days=30)
        self.assertGreaterEqual(deleted, 1)

    def test_export_json(self):
        """Export to JSON should produce valid file."""
        self.store.record_event("PostgresMemoryTest", "test_export",
                                {"key": "value"}, session_id=self.test_session)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            export_path = f.name

        try:
            result_path = self.store.export_json(path=export_path)
            self.assertEqual(result_path, export_path)
            self.assertTrue(os.path.exists(export_path))
            with open(export_path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertIn("memories", data)
            self.assertIn("count", data)
            self.assertGreaterEqual(data["count"], 1)
        finally:
            os.unlink(export_path)

    def test_query_real_errors(self):
        """query_real_errors should return error/warning counts."""
        self.store.record_event("PostgresMemoryTest", "real_err",
                                {"msg": "real error"}, severity="error",
                                session_id=self.test_session)
        result = self.store.query_real_errors(days=7)
        self.assertIn("total_errors", result)
        self.assertIn("total_warnings", result)

    def test_auto_vacuum_stub(self):
        """Auto-vacuum stubs should not raise."""
        self.store.start_auto_vacuum()
        self.store.stop_auto_vacuum()
        status = self.store.auto_vacuum_status
        self.assertIsInstance(status, dict)
        self.assertIn("running", status)

    def test_integrity_check_returns_dict(self):
        """integrity_check() should return a dict with expected keys."""
        result = self.store.integrity_check()
        self.assertIsInstance(result, dict)
        self.assertIn("passed", result)
        self.assertIn("ok", result)
        self.assertIn("errors", result)
        self.assertIn("database", result)
        self.assertIn("memories_rows", result)
        self.assertIn("sessions_rows", result)
        self.assertIn("indexes", result)

    def test_integrity_check_passes(self):
        """integrity_check should pass on a healthy database."""
        result = self.store.integrity_check()
        self.assertTrue(result["passed"])
        self.assertEqual(result["errors"], [])

    def test_integrity_check_has_indexes(self):
        """integrity_check should report all indexes."""
        result = self.store.integrity_check()
        self.assertGreaterEqual(len(result["indexes"]), 5,
                                "Should have at least 5 indexes across memories + sessions")

    def test_is_test_artifact_returns_true_for_nonexistent(self):
        """_is_test_artifact should filter nonexistent.exe test artifacts."""
        result = self.store._is_test_artifact({
            "data": {"path": "nonexistent.exe"},
            "event_type": "binanalysis.nm_error",
            "source": "BinaryAnalysis",
        })
        self.assertTrue(result)

    def test_is_test_artifact_returns_false_for_real(self):
        """_is_test_artifact should pass through real error data."""
        result = self.store._is_test_artifact({
            "data": {"msg": "GPU not detected"},
            "event_type": "gpu_error",
            "source": "EveUnleashed",
        })
        self.assertFalse(result)

    def test_query_real_errors_filters_artifacts(self):
        """query_real_errors should now filter test artifacts (not a stub)."""
        # Record both a real error and a test artifact
        self.store.record_event("PostgresMemoryTest", "real_err",
                                {"msg": "real error"}, severity="error",
                                session_id=self.test_session)
        self.store.record_event("PostgresMemoryTest", "test_err",
                                {"path": "nonexistent.exe"}, severity="error",
                                session_id=self.test_session)
        result = self.store.query_real_errors(days=7)
        self.assertIn("filtered_errors", result)
        self.assertIn("total_errors", result)

    def test_validate_empty_source_raises(self):
        """Empty source should raise ValueError."""
        with self.assertRaises(ValueError):
            self.store._validate_event_args("", "type", {}, "info", None)

    def test_validate_invalid_severity_raises(self):
        """Invalid severity should raise ValueError."""
        with self.assertRaises(ValueError):
            self.store._validate_event_args("Src", "type", {}, "bad_severity", None)

    def test_validate_valid_severity_normalizes(self):
        """Valid severity should be normalized to lowercase."""
        result = self.store._validate_event_args("Src", "type", {}, "WARNING", None)
        self.assertEqual(result, "warning")

    def test_validate_tags_must_be_list(self):
        """Non-list tags should raise ValueError."""
        with self.assertRaises(ValueError):
            self.store._validate_event_args("Src", "type", {}, "info", "not_a_list")

    def test_backup_creates_file(self):
        """backup() should create a SQL dump file."""
        import tempfile
        target = tempfile.mktemp(suffix=".sql")
        try:
            result = self.store.backup(target)
            self.assertEqual(result, target)
            self.assertTrue(os.path.exists(target))
            with open(target, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("INSERT INTO", content,
                          "Backup should contain INSERT statements")
            self.assertIn("memories", content,
                          "Backup should include memories table")
        finally:
            os.unlink(target)


class TestPostgresMemoryStoreMetadata(unittest.TestCase):
    """Test PostgresMemoryStore metadata and import."""

    def test_version_defined(self):
        """VERSION should be defined."""
        from CORE.postgres_memory import VERSION
        self.assertIsInstance(VERSION, str)
        self.assertGreater(len(VERSION), 0)

    def test_import_error_without_psycopg2(self):
        """Without psycopg2 the store must fail loudly, and name the requirement.

        The predecessor asserted `"psycopg2" in (sys.modules if _HAS_PSYCOPG2
        else [])`: membership in the EMPTY list whenever psycopg2 is absent, so
        on a host without psycopg2 (this one) it could never pass. Its own body
        comment -- "This test always passes since it tests the ImportError
        path" -- was the false belief that kept it. The contract worth testing
        is the ImportError that `PostgresMemoryStore.__init__` raises, and that
        its message tells the operator what to install.
        """
        from CORE.postgres_memory import PostgresMemoryStore, _HAS_PSYCOPG2
        if _HAS_PSYCOPG2:
            self.skipTest("psycopg2 installed; the ImportError path is unreachable")
        with self.assertRaises(ImportError) as ctx:
            PostgresMemoryStore()
        self.assertIn("psycopg2", str(ctx.exception))


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
