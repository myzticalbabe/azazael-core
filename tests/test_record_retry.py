# coding: utf-8
"""Tests for record_event lock-contention retry (monitoring shift fix).

  1. Two "database is locked" failures then success -> recorded, 2 backoffs.
  2. Persistent lock -> raises after exactly 3 attempts (fail-open upstream).
  3. Normal path unaffected (single attempt, returns rowid).
"""
import sqlite3
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE import local_memory as lm
from CORE.local_memory import LocalMemoryStore


class TestRecordRetry(unittest.TestCase):
    def _store(self, tmpdir):
        return LocalMemoryStore(db_path=str(Path(tmpdir) / "t.db"))

    def test_retries_then_records(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            store = self._store(td)
            real_get = LocalMemoryStore._get_conn.__get__(store, LocalMemoryStore)
            calls = {"n": 0, "sleeps": []}
            orig_sleep = lm.time.sleep
            lm.time.sleep = lambda s: calls["sleeps"].append(s)
            try:
                def flaky():
                    calls["n"] += 1
                    if calls["n"] < 3:
                        raise sqlite3.OperationalError("database is locked")
                    return real_get()
                store._get_conn = flaky
                mem_id = store.record_event(source="test", event_type="retry_probe",
                                            data={"k": 1})
            finally:
                lm.time.sleep = orig_sleep
            self.assertGreater(mem_id, 0)
            self.assertEqual(calls["n"], 3)
            self.assertEqual(len(calls["sleeps"]), 2)

    def test_gives_up_after_three(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            store = self._store(td)
            calls = {"n": 0}
            orig_sleep = lm.time.sleep
            lm.time.sleep = lambda s: None
            try:
                def always_locked():
                    calls["n"] += 1
                    raise sqlite3.OperationalError("database is locked")
                store._get_conn = always_locked
                with self.assertRaises(sqlite3.OperationalError):
                    store.record_event(source="test", event_type="retry_probe")
            finally:
                lm.time.sleep = orig_sleep
            self.assertEqual(calls["n"], 3)

    def test_normal_path_single_attempt(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            store = self._store(td)
            mem_id = store.record_event(source="test", event_type="retry_probe",
                                        data={"k": 2})
            self.assertGreater(mem_id, 0)


if __name__ == "__main__":
    unittest.main()
