"""200-thread concurrency stress test for DirectMemory under 3.14t free-threading."""
import pytest

pytestmark = pytest.mark.order

import sys
import json
import tempfile
import os
import threading
import time
from pathlib import Path

sys.path.insert(0, "src")


@pytest.fixture(autouse=True)
# start of the recorder guard release
#
# 200 threads writing at once is exactly the shape the recorder guard throttles,
# and it is process-wide, so a previous test's burst leaves the bucket empty and
# this file's writes are dropped -- the store then reports fewer rows than the
# test wrote and the count assertion fails on the guard, not on a lost update.
# The thread-safety claim is what this file tests; released for each test.
def _recorder_guard_released(monkeypatch):
    from CORE import recorder_guard
    monkeypatch.setattr(recorder_guard, "_DISABLED", True, raising=False)
    recorder_guard.guard().reset()


def _setup_temp_db():
    old_env = os.environ.get("AZAZAEL_MEMORY_DIR", "")
    tmpdir = Path(tempfile.mkdtemp())
    os.environ["AZAZAEL_MEMORY_DIR"] = str(tmpdir)
    from CORE import direct_memory
    direct_memory.DB_PATH = direct_memory._get_db_path()
    return tmpdir, old_env


def _teardown_temp_db(tmpdir, old_env):
    if old_env:
        os.environ["AZAZAEL_MEMORY_DIR"] = old_env
    else:
        os.environ.pop("AZAZAEL_MEMORY_DIR", None)
    import shutil
    try:
        shutil.rmtree(str(tmpdir))
    except Exception:
        pass


def test_200_concurrent_writes():
    """200 threads writing to DirectMemory simultaneously."""
    tmpdir, old_env = _setup_temp_db()
    try:
        from CORE.direct_memory import DirectMemory

        errors = []
        lock = threading.Lock()

        def worker(worker_id: int):
            try:
                dm = DirectMemory(session_id=f"stress_{worker_id:04d}")
                for i in range(10):
                    dm.record(f"stress_event", {"worker": worker_id, "iter": i},
                              tags=["stress-test"])
                dm.end()
            except Exception as e:
                with lock:
                    errors.append((worker_id, str(e)))

        threads = []
        for i in range(200):
            t = threading.Thread(target=worker, args=(i,), daemon=True)
            threads.append(t)

        start = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        elapsed = time.perf_counter() - start

        assert len(errors) == 0, f"Errors: {errors[:5]}"

        # Verify total events
        stats = DirectMemory.summary_stats()
        total = stats.get("total_events", 0)
        expected = 200 * 10  # Each worker records 10 events + session_start/end
        # Each session has ~12 events (start + 10 + end)
        print(f"  Concurrent writes: 200 threads x 10 events = {total} total ({elapsed:.2f}s)")
        assert total >= 2000, f"Expected >=2000 events, got {total}"
    finally:
        _teardown_temp_db(tmpdir, old_env)


def test_200_concurrent_mixed_ops():
    """200 threads doing mixed read/write operations."""
    tmpdir, old_env = _setup_temp_db()
    try:
        from CORE.direct_memory import DirectMemory

        # Seed some data first
        dm = DirectMemory(session_id="seed")
        dm.record("seed_event", {"v": 1})
        dm.end()

        errors = []
        lock = threading.Lock()

        def worker(worker_id: int):
            try:
                dm = DirectMemory(session_id=f"mixed_{worker_id:04d}")
                dm.record("write", {"worker": worker_id}, tags=["write"])
                # Read from the same session
                events = dm.list_events(limit=5)
                dm.end()
                # Static read
                stats = DirectMemory.summary_stats()
                assert isinstance(stats, dict), "summary_stats should return dict"
            except Exception as e:
                with lock:
                    errors.append((worker_id, str(e)))

        threads = []
        for i in range(200):
            t = threading.Thread(target=worker, args=(i,), daemon=True)
            threads.append(t)

        start = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        elapsed = time.perf_counter() - start
        assert len(errors) == 0, f"Errors: {errors[:5]}"
        print(f"  Mixed ops: 200 threads completed in {elapsed:.2f}s")
    finally:
        _teardown_temp_db(tmpdir, old_env)


def test_session_gc_under_load():
    """Run session_gc while other threads are writing."""
    tmpdir, old_env = _setup_temp_db()
    try:
        from CORE.direct_memory import DirectMemory

        errors = []
        lock = threading.Lock()
        stop_gc = threading.Event()

        def writer(worker_id: int):
            try:
                while not stop_gc.is_set():
                    dm = DirectMemory(session_id=f"gc_{worker_id:04d}_{int(time.time()*1000)}")
                    dm.record("gc_test", {"w": worker_id})
                    dm.end()
                    time.sleep(0.001)
            except Exception as e:
                with lock:
                    errors.append(("writer", str(e)))

        def gc_worker():
            try:
                while not stop_gc.is_set():
                    DirectMemory.session_gc(max_age_hours=0)  # Age 0 = close all open
                    time.sleep(0.01)
            except Exception as e:
                with lock:
                    errors.append(("gc", str(e)))

        writers = [threading.Thread(target=writer, args=(i,), daemon=True) for i in range(10)]
        gc_threads = [threading.Thread(target=gc_worker, daemon=True) for _ in range(3)]

        for t in writers + gc_threads:
            t.start()

        time.sleep(2.0)
        stop_gc.set()

        for t in writers:
            t.join(timeout=5)
        for t in gc_threads:
            t.join(timeout=5)

        assert len(errors) == 0, f"Errors: {errors[:5]}"
        print(f"  GC under load: 10 writers x 3 GC threads ran for 2s — no errors")
    finally:
        _teardown_temp_db(tmpdir, old_env)
