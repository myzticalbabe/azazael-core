"""Integration tests for CORE/freethreading.py — FreeThreadPool, concurrent execution, thread safety."""

import sys
import os
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from CORE.freethreading import (
    is_free_threaded,
    FreeThreadPool,
    ThreadSafeCounter,
    ThreadSafeDict,
    ThreadSafeList,
    ConcurrentTaskRunner,
    DeviceParallelConfig,
    benchmark_parallel,
)


# ═══════════════════════════════════════════════════════════════════
# Free-Threading Detection
# ═══════════════════════════════════════════════════════════════════


class TestFreeThreadingDetection:
    def test_is_free_threaded_returns_bool(self):
        """is_free_threaded() should return a boolean."""
        result = is_free_threaded()
        assert isinstance(result, bool)

    def test_free_threaded_status_stable(self):
        """Free-threading status should be stable across calls."""
        r1 = is_free_threaded()
        r2 = is_free_threaded()
        assert r1 == r2


# ═══════════════════════════════════════════════════════════════════
# FreeThreadPool Basic Tests
# ═══════════════════════════════════════════════════════════════════


class TestFreeThreadPool:
    def test_pool_context_manager(self):
        """FreeThreadPool should work as context manager."""
        with FreeThreadPool(max_workers=4) as pool:
            assert pool._executor is not None

    def test_pool_submit_returns_future(self):
        """Submitting a task should return a Future."""
        with FreeThreadPool(max_workers=4) as pool:
            future = pool.submit(lambda x: x + 1, 41)
            assert future.result() == 42

    def test_pool_multiple_tasks(self):
        """Multiple tasks should all complete."""
        with FreeThreadPool(max_workers=4) as pool:
            futures = [pool.submit(lambda i=i: i * 2, i) for i in range(10)]
            results = [f.result() for f in futures]
            assert results == [0, 2, 4, 6, 8, 10, 12, 14, 16, 18]

    def test_pool_default_workers(self):
        """Default max_workers should be cpu_count."""
        pool = FreeThreadPool()
        assert pool.max_workers == os.cpu_count()

    def test_pool_outside_context_raises(self):
        """Accessing executor outside context should raise."""
        pool = FreeThreadPool()
        with pytest.raises(RuntimeError):
            _ = pool.executor

    def test_pool_map(self):
        """Pool.map should work like ThreadPoolExecutor.map."""
        with FreeThreadPool(max_workers=4) as pool:
            results = list(pool.map(lambda x: x * 2, range(5)))
            assert results == [0, 2, 4, 6, 8]

    def test_pool_info(self):
        """Pool.info() should return expected keys."""
        info = FreeThreadPool.info()
        assert "free_threaded" in info
        assert "cpu_count" in info
        assert "recommended_workers" in info
        assert info["cpu_count"] == os.cpu_count()


# ═══════════════════════════════════════════════════════════════════
# Parallel Execution — Work-Stealing Patterns
# ═══════════════════════════════════════════════════════════════════


class TestParallelWorkStealing:
    """Test work-stealing patterns with varying workloads."""

    def test_uniform_workload(self):
        """All workers should complete uniform tasks."""
        with FreeThreadPool(max_workers=8) as pool:
            futures = [pool.submit(lambda: sum(range(1000))) for _ in range(20)]
            results = [f.result() for f in futures]
            assert len(results) == 20
            assert all(r == sum(range(1000)) for r in results)

    def test_variable_workload(self):
        """Variable-length tasks should still complete."""
        def variable_work(n):
            time.sleep(n * 0.001)
            return n

        with FreeThreadPool(max_workers=4) as pool:
            futures = [pool.submit(variable_work, i) for i in [0.1, 0.05, 0.2, 0.01]]
            results = [f.result() for f in futures]
            assert results == [0.1, 0.05, 0.2, 0.01]

    def test_large_task_count(self):
        """Many small tasks should all complete."""
        with FreeThreadPool(max_workers=8) as pool:
            futures = [pool.submit(lambda i=i: i ** 2, i) for i in range(100)]
            results = [f.result() for f in futures]
            assert len(results) == 100
            assert results[-1] == 99 ** 2

    def test_concurrent_write_safety(self):
        """Concurrent writes to shared list should work with lock."""
        results = []
        lock = threading.Lock()

        def safe_append(val):
            with lock:
                results.append(val)
            return True

        with FreeThreadPool(max_workers=8) as pool:
            futures = [pool.submit(safe_append, i) for i in range(50)]
            [f.result() for f in futures]

        assert len(results) == 50
        assert sorted(results) == list(range(50))


# ═══════════════════════════════════════════════════════════════════
# ConcurrentTaskRunner Tests
# ═══════════════════════════════════════════════════════════════════


class TestConcurrentTaskRunner:
    def test_empty_runner(self):
        """Empty runner should return empty results."""
        runner = ConcurrentTaskRunner()
        result = runner.run_all()
        assert result["devices_used"] == 0
        assert result["errors"] == []

    def test_single_task(self):
        """Single task should complete."""
        runner = ConcurrentTaskRunner()
        runner.set_task("cpu", lambda: {"status": "ok"})
        result = runner.run_all()
        assert result["devices_used"] == 1
        assert result["errors"] == []
        assert result["results"].get("cpu_result") == {"status": "ok"}

    def test_multiple_tasks(self):
        """Multiple tasks should all complete."""
        runner = ConcurrentTaskRunner()
        runner.set_task("cpu1", lambda: {"id": 1})
        runner.set_task("cpu2", lambda: {"id": 2})
        result = runner.run_all()
        assert result["devices_used"] == 2
        assert result["errors"] == []

    def test_task_exception_handling(self):
        """Task that raises should be captured as error."""
        runner = ConcurrentTaskRunner()
        runner.set_task("cpu", lambda: 1 / 0)
        result = runner.run_all()
        assert len(result["errors"]) == 1
        assert "division by zero" in result["errors"][0]["error"]

    def test_task_duration_tracking(self):
        """Task duration should be tracked."""
        runner = ConcurrentTaskRunner()
        runner.set_task("cpu", lambda: time.sleep(0.05))
        result = runner.run_all()
        elapsed = result["results"].get("cpu_elapsed_sec", 0)
        assert elapsed >= 0.05


# ═══════════════════════════════════════════════════════════════════
# ThreadSafe Data Structures
# ═══════════════════════════════════════════════════════════════════


class TestThreadSafeCounter:
    def test_initial_value(self):
        """Counter should start at given initial value."""
        c = ThreadSafeCounter(10)
        assert c.value == 10

    def test_increment(self):
        """Increment should increase value."""
        c = ThreadSafeCounter()
        c.increment()
        assert c.value == 1

    def test_decrement(self):
        """Decrement should decrease value."""
        c = ThreadSafeCounter(5)
        c.decrement()
        assert c.value == 4

    def test_increment_multiple(self):
        """Multiple increments should accumulate."""
        c = ThreadSafeCounter()
        for _ in range(100):
            c.increment()
        assert c.value == 100

    def test_reset(self):
        """Reset should set value to 0."""
        c = ThreadSafeCounter(50)
        c.reset()
        assert c.value == 0


class TestThreadSafeDict:
    def test_basic_ops(self):
        """Basic get/set should work."""
        d = ThreadSafeDict()
        d["key"] = "value"
        assert d["key"] == "value"

    def test_contains(self):
        """Contains check should work."""
        d = ThreadSafeDict()
        d["a"] = 1
        assert "a" in d
        assert "b" not in d

    def test_get_default(self):
        """Get with default should return default for missing key."""
        d = ThreadSafeDict()
        assert d.get("missing", "default") == "default"

    def test_len(self):
        """Len should reflect number of keys."""
        d = ThreadSafeDict()
        d["a"] = 1
        d["b"] = 2
        assert len(d) == 2

    def test_concurrent_access(self):
        """Concurrent access should not cause errors."""
        d = ThreadSafeDict()
        lock = threading.Lock()
        def writer(start, count):
            for i in range(count):
                d[f"k{start + i}"] = start + i

        threads = []
        for t in range(4):
            th = threading.Thread(target=writer, args=(t * 25, 25))
            threads.append(th)
            th.start()
        for th in threads:
            th.join()

        assert len(d) == 100


class TestThreadSafeList:
    def test_append(self):
        """Append should add items."""
        lst = ThreadSafeList()
        lst.append(1)
        lst.append(2)
        assert len(lst) == 2

    def test_get_all(self):
        """get_all should return all items."""
        lst = ThreadSafeList(["a", "b", "c"])
        assert lst.get_all() == ["a", "b", "c"]

    def test_contains(self):
        """Contains should work."""
        lst = ThreadSafeList(["x", "y"])
        assert "x" in lst
        assert "z" not in lst

    def test_extend(self):
        """Extend should add multiple items."""
        lst = ThreadSafeList([1, 2])
        lst.extend([3, 4])
        assert len(lst) == 4
        assert lst.get_all() == [1, 2, 3, 4]


# ═══════════════════════════════════════════════════════════════════
# DeviceParallelConfig Tests
# ═══════════════════════════════════════════════════════════════════


class TestDeviceParallelConfig:
    def test_cpu_defaults(self):
        """CPU config should have sensible defaults."""
        cfg = DeviceParallelConfig.for_cpu()
        assert cfg.device_name == "CPU"
        assert cfg.max_workers > 0
        assert cfg.per_worker_memory_mb == 128

    def test_gpu_defaults(self):
        """GPU config should have GPU-specific defaults."""
        cfg = DeviceParallelConfig.for_gpu()
        assert cfg.device_name == "GPU"
        assert cfg.min_batch_size == 4


# ═══════════════════════════════════════════════════════════════════
# Standalone Runner
# ═══════════════════════════════════════════════════════════════════


def _run_standalone():
    """Run all tests standalone (for an environment where pytest is unusable)."""
    passed = 0
    failed = 0
    errors = []
    print("Free-Threading Integration Tests")
    print("=" * 60)
    print()

    for name, obj in sorted(globals().items(), key=lambda x: x[0]):
        if isinstance(obj, type) and name.startswith("Test"):
            print(f"[{name}]")
            inst = obj()
            for mn in sorted(dir(inst)):
                if mn.startswith("test_"):
                    try:
                        sys.stdout.flush()
                        getattr(inst, mn)()
                        print(f"  [OK] {mn}")
                        passed += 1
                    except Exception as e:
                        print(f"  [FAIL] {mn}: {e}")
                        failed += 1
                        errors.append(f"{name}.{mn}: {e}")
            print()

    total = passed + failed
    print(f"  Total: {total} | Passed: {passed} | Failed: {failed}")
    if errors:
        print("  Errors:")
        for e in errors[:5]:
            print(f"    {e}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_standalone())
