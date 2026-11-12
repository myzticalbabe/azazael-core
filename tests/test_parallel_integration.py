"""Integration tests for parallel execution — FreeThreadPool work-stealing,
ClusterWORMZ parallel execution, timeout/cancel handling, and real worm parallelism.

These tests exercise the parallel execution paths that unit tests don't cover:
- FreeThreadPool under unbalanced workloads (work-stealing)
- Timeout and cancel handling in ClusterWORMZ.execute_all()
- Retry logic in parallel context
- Real registered worms executing in parallel (marked slow)
- Stress testing with many concurrent tasks

NOTE: Parallel execution with the 'full' cluster has been observed to hang on some
systems (>120s). The root cause is under investigation — may be related to specific
worm modules (TRIMZ, CLOCKZ) not being registered in the worm registry. Real worm
tests use 'system' cluster which has predictable behavior.
"""

import sys
import os
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from CORE.freethreading import FreeThreadPool
from CORE.WORMS.CLUSTERWORMS import (
    ClusterWORMZ, WormConfig, WormExecutor, ClusterState,
)

# ═══════════════════════════════════════════════════════════════════
# FreeThreadPool — Stress & Work-Stealing
# ═══════════════════════════════════════════════════════════════════


class TestFreeThreadPoolStress:
    """Stress tests for FreeThreadPool with many concurrent tasks."""

    @pytest.mark.slow
    def test_100_tasks_parallel(self):
        """100 CPU-bound tasks should all complete via FreeThreadPool."""
        with FreeThreadPool(max_workers=8) as pool:
            futures = [pool.submit(sum, range(i * 1000)) for i in range(100)]
            results = [f.result(timeout=30) for f in futures]
        assert len(results) == 100
        assert results[0] == sum(range(0))
        assert results[50] == sum(range(50 * 1000))
        assert results[99] == sum(range(99 * 1000))

    @pytest.mark.slow
    def test_500_fast_tasks(self):
        """500 tiny tasks should complete efficiently."""
        with FreeThreadPool(max_workers=16) as pool:
            futures = [pool.submit(lambda i=i: i, i) for i in range(500)]
            results = [f.result(timeout=10) for f in futures]
        assert len(results) == 500
        assert sorted(results) == list(range(500))

    def test_pool_reuse_across_contexts(self):
        """Multiple with-blocks should work sequentially."""
        for _ in range(5):
            with FreeThreadPool(max_workers=4) as pool:
                results = [pool.submit(lambda i=i: i * 2, i).result() for i in range(10)]
            assert len(results) == 10

    def test_concurrent_writes_no_loss(self):
        """Many concurrent writes to a shared list with lock should not lose data."""
        shared = []
        lock = threading.Lock()

        def safe_work(n):
            with lock:
                shared.append(n)
            return n * 2

        with FreeThreadPool(max_workers=8) as pool:
            futures = [pool.submit(safe_work, i) for i in range(200)]
            results = [f.result(timeout=30) for f in futures]

        assert len(results) == 200
        assert len(shared) == 200
        assert sorted(shared) == list(range(200))


class TestWorkStealingUnbalanced:
    """Integration tests for work-stealing with unbalanced workloads.

    Real-world scenario: ClusterWORMZ executes worms with vastly different
    cooldowns (e.g., MetaCluster at 5s vs ColdArchivist at 300s).
    Work-stealing should keep all workers busy instead of having some
    idle while others are overloaded.
    """

    def test_fast_and_slow_tasks(self):
        """Mix of fast and slow tasks should all complete."""
        def fast_task():
            return "fast"

        def slow_task():
            time.sleep(0.05)
            return "slow"

        with FreeThreadPool(max_workers=4) as pool:
            fast_futures = [pool.submit(fast_task) for _ in range(10)]
            slow_futures = [pool.submit(slow_task) for _ in range(2)]
            all_futures = fast_futures + slow_futures
            results = [f.result(timeout=10) for f in all_futures]

        assert results.count("fast") == 10
        assert results.count("slow") == 2

    @pytest.mark.slow
    def test_balanced_completion_time(self):
        """Work-stealing should prevent long tail on unbalanced workloads.

        With 4 workers and 4 slow (0.1s) + 4 fast tasks, total time should
        be ~0.2s (2 batches of 2 slow tasks) not ~0.4s (serialized).
        """
        def fast():
            return 1

        def slow():
            time.sleep(0.1)
            return 10

        t0 = time.perf_counter()
        with FreeThreadPool(max_workers=4) as pool:
            futures = [pool.submit(slow) for _ in range(4)] + \
                      [pool.submit(fast) for _ in range(4)]
            results = [f.result(timeout=30) for f in futures]
        elapsed = time.perf_counter() - t0

        assert len(results) == 8
        assert sum(results) == 44
        # 4 slow tasks with 4 workers = 2 batches at 0.1s each = ~0.2s
        assert elapsed < 1.0, f"Took {elapsed:.2f}s — work-stealing may not be effective"

    @pytest.mark.slow
    def test_many_short_tasks_with_few_long(self):
        """Many short tasks shouldn't starve behind a few long ones."""
        completed = []
        lock = threading.Lock()

        def short_task(i):
            with lock:
                completed.append(("short", i))
            return i

        def long_task():
            time.sleep(0.2)
            with lock:
                completed.append(("long", -1))
            return -1

        with FreeThreadPool(max_workers=8) as pool:
            long_futures = [pool.submit(long_task) for _ in range(2)]
            time.sleep(0.01)
            short_futures = [pool.submit(short_task, i) for i in range(50)]
            all_results = [f.result(timeout=30) for f in long_futures + short_futures]

        assert len(all_results) == 52
        assert all_results.count(-1) == 2
        short_indices = [i for ctype, i in completed if ctype == "short"]
        assert len(short_indices) == 50


# ═══════════════════════════════════════════════════════════════════
# ClusterWORMZ Parallel Execution — Timeout & Cancel
# ═══════════════════════════════════════════════════════════════════


class TestClusterParallelTimeout:
    """Integration tests for timeout handling in ClusterWORMZ parallel execution.

    Covers the as_completed(timeout=X) logic in execute_all() and
    the future.cancel() path for timed-out worms.
    Uses 'system' cluster (3 worms: SYSTEMZ, PROCESSZ, DISKZ) for predictable behavior.
    """

    def test_short_timeout_does_not_crash(self):
        """Very short timeout should not crash."""
        cluster = ClusterWORMZ("system")
        cluster.set_parallel(True)
        cluster.set_timeout(0.01)
        results = cluster.execute_all()
        assert len(results) == len([c for c in cluster.configs if c.enabled])
        for r in results:
            assert "name" in r
            assert "success" in r

    def test_parallel_timeout_cancel_no_crash(self):
        """Aggressive timeout should not crash."""
        cluster = ClusterWORMZ("system")
        cluster.set_parallel(True)
        cluster.set_timeout(0.005)
        results = cluster.execute_all()
        assert len(results) > 0
        for r in results:
            assert "name" in r
            assert "success" in r

    def test_long_timeout_all_complete(self):
        """Generous timeout should let all worms complete."""
        cluster = ClusterWORMZ("system")
        cluster.set_parallel(True)
        cluster.set_timeout(120)
        results = cluster.execute_all()
        assert len(results) == len([c for c in cluster.configs if c.enabled])
        timed_out = [r for r in results if r.get("timed_out")]
        assert len(timed_out) == 0


# ═══════════════════════════════════════════════════════════════════
# ClusterWORMZ Parallel Execution — Retry Logic
# ═══════════════════════════════════════════════════════════════════


class TestClusterParallelRetry:
    """Integration tests for retry logic in parallel execution context."""

    def test_retry_known_worm_in_parallel(self):
        """Retry should work for known worms in parallel mode."""
        cluster = ClusterWORMZ("system")
        cluster.set_parallel(True)
        if cluster.configs:
            cluster.configs[0].retry = 2
        results = cluster.execute_all()
        assert len(results) == len([c for c in cluster.configs if c.enabled])
        assert "name" in results[0]
        assert "success" in results[0]

    def test_retry_unknown_worm_in_context(self):
        """Retry should not crash for failing worms in parallel mode."""
        cluster = ClusterWORMZ("system")
        cluster.set_parallel(True)
        cluster.add_worm("NONEXISTENT_RETRY", "NONEXISTENT_RETRY", {"retry": 2})
        results = cluster.execute_all()
        missing = [r for r in results if r["name"] == "NONEXISTENT_RETRY"]
        assert len(missing) >= 1
        assert missing[0]["success"] is False


# ═══════════════════════════════════════════════════════════════════
# ClusterWORMZ Parallel Execution — Real Worms (Slow)
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.slow
class TestClusterParallelRealWorms:
    """Integration tests running actual registered worms in parallel.

    Marked slow because real worms make system calls (process enumeration,
    disk monitoring, network checks) that can take several seconds.
    """

    def test_system_cluster_parallel(self):
        """'system' cluster (3 worms) should execute all in parallel."""
        cluster = ClusterWORMZ("system")
        cluster.set_parallel(True)
        cluster.set_timeout(30)
        results = cluster.execute_all()
        assert len(results) == 3
        names = [r["name"] for r in results]
        assert "SYSTEMZ" in names
        assert "PROCESSZ" in names
        assert "DISKZ" in names

    def test_security_cluster_parallel(self):
        """'security' cluster (3 worms) should execute all in parallel."""
        cluster = ClusterWORMZ("security")
        cluster.set_parallel(True)
        cluster.set_timeout(30)
        results = cluster.execute_all()
        assert len(results) == 3
        names = [r["name"] for r in results]
        assert "PROCESSZ" in names
        assert "NETWORXZ" in names
        assert "SYSTEMZ" in names

    def test_category_built_cluster_parallel(self):
        """Cluster built from nature should execute worms in parallel."""
        cluster = ClusterWORMZ.build_from_category("orchestration")
        if cluster is not None and len(cluster.configs) > 0:
            cluster.set_parallel(True)
            cluster.set_timeout(30)
            results = cluster.execute_all()
            assert len(results) == len([c for c in cluster.configs if c.enabled])
            for r in results:
                assert "name" in r
                assert "success" in r
                assert "duration_ms" in r


# ═══════════════════════════════════════════════════════════════════
# Parallel → Sequential Switching
# ═══════════════════════════════════════════════════════════════════


class TestClusterParallelModeSwitching:
    """Verify switching between parallel and sequential modes works."""

    def test_parallel_then_sequential(self):
        """Parallel and sequential runs should yield same worm count."""
        cluster = ClusterWORMZ("system")
        target_worms = sorted([c.name for c in cluster.configs if c.enabled])

        cluster.set_parallel(True)
        par = cluster.execute_all()
        assert sorted([r["name"] for r in par]) == target_worms

        cluster.set_parallel(False)
        seq = cluster.execute_all()
        assert sorted([r["name"] for r in seq]) == target_worms

    def test_alternating_mode_no_state_leak(self):
        """Switching modes should not leak state between runs."""
        cluster = ClusterWORMZ("system")
        cluster.set_timeout(10)
        for parallel in [False, True, False, True]:
            cluster.set_parallel(parallel)
            results = cluster.execute_all()
            assert len(results) == 3
            assert cluster._execution_count > 0

    def test_parallel_then_aggregate(self):
        """Aggregation after parallel execution should work."""
        cluster = ClusterWORMZ("system")
        cluster.set_parallel(True)
        results = cluster.execute_all()
        agg = cluster.aggregate_results()
        assert agg.success_count + agg.failure_count == len(results)
        assert 0 <= agg.aggregated_metrics["success_rate"] <= 1


# ═══════════════════════════════════════════════════════════════════
# WormExecutor Parallel Safety
# ═══════════════════════════════════════════════════════════════════


class TestWormExecutorParallelSafety:
    """Verify WormExecutor is thread-safe when called from multiple threads."""

    def test_multiple_executors_parallel(self):
        """Multiple WormExecutors should work concurrently."""
        state = ClusterState("parallel_test")
        configs = [
            WormConfig(name="WORMZ", module="WORMZ"),
            WormConfig(name="DISKZ", module="DISKZ", timeout=10),
        ]
        results = []
        lock = threading.Lock()

        def run_executor(cfg):
            executor = WormExecutor(cfg.name, cfg, state)
            result = executor.execute()
            with lock:
                results.append(result)

        with FreeThreadPool(max_workers=4) as pool:
            futures = [pool.submit(run_executor, cfg) for cfg in configs]
            [f.result(timeout=30) for f in futures]

        assert len(results) == 2
        assert state.results["WORMZ"]["success"] is True
        assert "DISKZ" in state.results

    def test_executor_isolation(self):
        """Each executor should have isolated config state."""
        state = ClusterState("isolation_test")
        cfg_a = WormConfig(name="A", module="WORMZ")
        cfg_b = WormConfig(name="B", module="WORMZ")
        results = []
        lock = threading.Lock()

        def run(name, cfg):
            executor = WormExecutor(name, cfg, state)
            result = executor.execute()
            with lock:
                results.append(result)

        with FreeThreadPool(max_workers=4) as pool:
            futures = [pool.submit(run, "A", cfg_a), pool.submit(run, "B", cfg_b)]
            [f.result(timeout=30) for f in futures]

        assert len(results) == 2
        ra = [r for r in results if r["name"] == "A"][0]
        rb = [r for r in results if r["name"] == "B"][0]
        assert ra["success"] is True
        assert rb["success"] is True
        assert ra["duration_ms"] >= 0
        assert rb["duration_ms"] >= 0


# ═══════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════


class TestParallelEdgeCases:
    """Edge cases in parallel execution."""

    def test_no_enabled_worms(self):
        """Cluster with no enabled worms should return empty results."""
        cluster = ClusterWORMZ("system")
        cluster.set_parallel(True)
        for cfg in cluster.configs:
            cfg.enabled = False
        results = cluster.execute_all()
        assert isinstance(results, list)
        assert len(results) == 0

    def test_single_worm_parallel(self):
        """Single worm in parallel should return one result."""
        cluster = ClusterWORMZ("system")
        cluster.configs = [cluster.configs[0]]
        cluster.set_parallel(True)
        results = cluster.execute_all()
        assert len(results) == 1

    def test_parallel_with_custom_timeout_per_worm(self):
        """Per-worm timeout should not crash."""
        cluster = ClusterWORMZ("system")
        cluster.set_parallel(True)
        cluster.set_timeout(30)
        if cluster.configs:
            cluster.configs[0].timeout = 5
        results = cluster.execute_all()
        assert len(results) == len([c for c in cluster.configs if c.enabled])


# ═══════════════════════════════════════════════════════════════════
# Standalone Runner
# ═══════════════════════════════════════════════════════════════════


def _run_standalone():
    """Run all tests standalone (for 3.14t where pytest may segfault).
    Skips @pytest.mark.slow tests for faster validation.
    """
    passed = 0
    failed = 0
    errors = []
    skipped = 0
    print("Parallel Integration Tests (standalone — slow tests skipped)")
    print("=" * 60)
    print()

    for name, obj in sorted(globals().items(), key=lambda x: x[0]):
        if isinstance(obj, type) and name.startswith("Test"):
            is_slow = hasattr(obj, '__pytestmark__') and any(
                m.name == 'slow' for m in obj.__pytestmark__
            )
            if is_slow:
                print(f"[{name}] (SKIPPED — slow)")
                skipped += 1
                continue
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
                        import traceback
                        print(f"  [FAIL] {mn}: {e}")
                        traceback.print_exc()
                        failed += 1
                        errors.append(f"{name}.{mn}: {e}")
            print()

    total = passed + failed
    print(f"  Total: {total} | Passed: {passed} | Failed: {failed} | Skipped: {skipped}")
    if errors:
        print("  Errors:")
        for e in errors[:10]:
            print(f"    {e}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_standalone())
