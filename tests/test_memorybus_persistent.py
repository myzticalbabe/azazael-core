import os
import sys
import sqlite3
import unittest
from pathlib import Path

# Ensure PAMS is simulated as down before MemoryBus is imported
os.environ['PAMS_URL'] = 'http://127.0.0.1:99999'

# Add azazael/src to path (same pattern as other test files)
_src_dir = Path(__file__).resolve().parent.parent / "src"
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from CORE.MemoryBus import MemoryBus, BusMessage, _MEMORYBUS_DB_PATH
import CORE.MemoryBus as _bus_mod


class TestMemoryBusPersistentFallback(unittest.TestCase):

    def setUp(self):
        # Self-isolation (2026-09-28): this file DROPs memorybus_queue, so it
        # must never resolve that table against the live store no matter how
        # it is invoked (bare unittest, pytest without conftest, new runner).
        # Redirect BOTH the module global (all call sites read it live) and
        # this file's imported copy, then restore both in tearDown.
        import tempfile as _tf
        self._old_env = os.environ.get("AZAZAEL_MEMORY_DIR")
        self._old_global = _bus_mod._MEMORYBUS_DB_PATH
        self._tmpdir = _tf.mkdtemp(prefix="membus_test_")
        os.environ["AZAZAEL_MEMORY_DIR"] = self._tmpdir
        _fresh = _bus_mod._get_memorybus_db_path()
        _bus_mod._MEMORYBUS_DB_PATH = _fresh
        globals()["_MEMORYBUS_DB_PATH"] = _fresh
        self._clear_db()

    def tearDown(self):
        self._clear_db()
        import shutil as _sh
        _bus_mod._MEMORYBUS_DB_PATH = self._old_global
        globals()["_MEMORYBUS_DB_PATH"] = self._old_global
        if self._old_env is None:
            os.environ.pop("AZAZAEL_MEMORY_DIR", None)
        else:
            os.environ["AZAZAEL_MEMORY_DIR"] = self._old_env
        _sh.rmtree(self._tmpdir, ignore_errors=True)

    def _clear_db(self):
        """Helper to ensure a clean slate for the persistent queue database."""
        try:
            conn = sqlite3.connect(str(_MEMORYBUS_DB_PATH), timeout=2)
            conn.execute("DROP TABLE IF EXISTS memorybus_queue")
            conn.commit()
            conn.close()
        except Exception:
            pass

    def test_persistent_fallback_flow(self):
        """
        Validates:
        1. When PAMS is down, messages are persisted to memorybus_queue.
        2. Messages survive process restart.
        3. flush_local_queue() handles flushing and stats correctly.
        """
        # --- 1. Publish when PAMS is down ---
        bus = MemoryBus(use_local_fallback=True)
        self.assertFalse(bus._pams_available, "PAMS should be unavailable for this test")

        bus.publish("test.persist_1", "offline data 1")
        
        stats = bus.get_stats()
        self.assertEqual(stats['persistent_fallback_used'], 1)
        self.assertEqual(stats['persistent_queue_size'], 1)
        self.assertEqual(stats['local_queue_size'], 1)

        # --- 2. Process restart survival ---
        bus.close()
        del bus

        # Verify SQL table directly
        conn = sqlite3.connect(str(_MEMORYBUS_DB_PATH))
        rows = conn.execute("SELECT topic, content, flushed FROM memorybus_queue").fetchall()
        conn.close()
        
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "test.persist_1")
        self.assertEqual(rows[0][2], 0)  # flushed = 0

        # Create new bus instance (simulating restart)
        relived_bus = MemoryBus(use_local_fallback=True)
        stats2 = relived_bus.get_stats()
        self.assertEqual(stats2['persistent_queue_size'], 1)

        # Publish a second message to test batched queues
        relived_bus.publish("test.persist_2", "offline data 2")
        self.assertEqual(relived_bus.get_stats()['persistent_queue_size'], 2)
        
        # Local queue for THIS instance should be 1, because the first message is only in DB now
        self.assertEqual(relived_bus.get_stats()['local_queue_size'], 1)

        # --- 3 & 4. PAMS comes online -> flush_local_queue ---
        relived_bus._pams_available = True
        
        # Mock _post_to_pams to simulate successful network delivery
        posted_topics = []
        def mock_post(topic, content, metadata, source):
            posted_topics.append(topic)
            return len(posted_topics)  # Fake message ID
        
        relived_bus._post_to_pams = mock_post
        
        flushed_count = relived_bus.flush_local_queue()
        
        self.assertEqual(flushed_count, 3, "Should flush 1 local + 2 persistent DB items")
        self.assertIn("test.persist_1", posted_topics)
        self.assertIn("test.persist_2", posted_topics)

        # Check accurate stat tracking after flush
        stats3 = relived_bus.get_stats()
        self.assertEqual(stats3['persistent_flushed'], 2, "2 DB rows flushed to PAMS")
        self.assertEqual(stats3['persistent_queue_size'], 0)
        self.assertEqual(stats3['local_queue_size'], 0)
        
        relived_bus.close()

    def test_close_persists_remaining_in_memory_queue(self):
        """
        Validates:
        close() persists any lingering in-memory messages to SQLite before clearing.
        """
        bus = MemoryBus(use_local_fallback=True)
        
        # Backdoor a message directly into the ephemeral local queue 
        # (simulating a scenario where DB write previously failed but memory succeeded)
        msg = BusMessage(
            topic="test.close_persist",
            content="in memory only",
            metadata={},
            source="test"
        )
        with bus._local_queue_lock:
            bus._local_queue.append(msg)

        stats_before = bus.get_stats()
        self.assertEqual(stats_before['persistent_queue_size'], 0, "No data should be in DB yet")
        self.assertEqual(stats_before['local_queue_size'], 1)
        
        # Trigger graceful teardown
        bus.close()
        
        # Verify memory was persisted to disk on close
        conn = sqlite3.connect(str(_MEMORYBUS_DB_PATH))
        rows = conn.execute("SELECT topic FROM memorybus_queue WHERE flushed=0").fetchall()
        conn.close()
        
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "test.close_persist")


if __name__ == '__main__':
    unittest.main()
