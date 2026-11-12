# coding: utf-8
"""
Unit tests for AgentPool (azazael/src/CORE/AgentPool.py)

Run with: python tests/test_pool.py
"""

import sys, os, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
os.environ['PAMS_URL'] = 'http://127.0.0.1:99999'

from CORE.AgentPool import AgentPool, PoolTask, PoolStats


def test_pool_creation():
    """Test pool can be created with default params."""
    pool = AgentPool(max_workers=4, record_to_pams=False)
    assert pool.max_workers == 4
    assert pool.is_active is True
    stats = pool.get_stats()
    assert stats['tasks_submitted'] == 0
    assert stats['version'] == '1.1.0'
    pool.close()


def test_pool_context_manager():
    """Test pool works as context manager."""
    with AgentPool(max_workers=2, record_to_pams=False) as pool:
        assert pool.is_active is True
    # After exit, pool should be closed
    assert pool.is_active is False


def test_single_submit():
    """Test submitting a single task via submit()."""
    with AgentPool(max_workers=2, record_to_pams=False) as pool:
        def square(x):
            return x * x
        fut = pool.submit(square, 5)
        result = fut.result()
        assert result == 25


def test_multiple_submit():
    """Test multiple concurrent submissions."""
    with AgentPool(max_workers=4, record_to_pams=False) as pool:
        def double(x):
            return x * 2
        
        futures = [pool.submit(double, i) for i in range(10)]
        results = [f.result() for f in futures]
        assert results == [0, 2, 4, 6, 8, 10, 12, 14, 16, 18]


def test_map_basic():
    """Test pool.map() with a simple function."""
    with AgentPool(max_workers=4, record_to_pams=False) as pool:
        def add_one(x):
            return x + 1
        
        results = pool.map(add_one, range(10))
        assert len(results) == 10
        assert results == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]


def test_map_empty():
    """Test pool.map() with empty iterable."""
    with AgentPool(max_workers=2, record_to_pams=False) as pool:
        results = pool.map(lambda x: x, [])
        assert results == []


def test_submit_error_handling():
    """Test that errors in submitted tasks propagate correctly."""
    with AgentPool(max_workers=2, record_to_pams=False) as pool:
        def fail(x):
            if x < 0:
                raise ValueError('Negative!')
            return x
        
        fut_ok = pool.submit(fail, 5)
        assert fut_ok.result() == 5
        
        fut_bad = pool.submit(fail, -1)
        try:
            fut_bad.result()
            assert False, 'Should have raised'
        except ValueError as e:
            assert 'Negative!' in str(e)


def test_benchmark_basic():
    """Test benchmark runs and returns results."""
    with AgentPool(max_workers=4, record_to_pams=False) as pool:
        def math_work():
            total = sum(i * i for i in range(1000))
            return total
        
        results = pool.benchmark(math_work, worker_levels=[1, 2], iterations_per_level=500)
        assert 1 in results
        assert 2 in results
        assert results[1]['workers'] == 1
        assert results[2]['workers'] == 2
        assert results[1]['throughput_per_sec'] > 0
        assert results[1]['duration_sec'] > 0


def test_two_workers_run_concurrently():
    """The real claim behind "speedup": two pool workers are in flight at once.

    This replaced a wall-clock speedup assertion (`results[2]['speedup'] >= 1.0`)
    which measured the machine rather than the pool. Measured 2026-09-22 on the
    aggregate suite (Error #201, ADR-015): the workload was 200 tasks of
    `sum(i**2 for i in range(5000))`, ~10 ms in total, so spawning the level-2
    worker cost more than the parallelism bought; raising the workload and taking
    the best of 3 attempts still read 0.687 while a graph rebuild shared the CPU,
    because a *timing* signal cannot distinguish "the pool serialised its workers"
    from "something else wanted the CPU".

    A two-party rendezvous can: both tasks block until BOTH are running, so the
    call returns only if the pool really has two workers in flight, and it
    returns at the same speed on an idle box and a busy one. If the pool ever
    serialised, the barrier would time out and the failure names concurrency
    instead of naming the load.

    The throughput numbers themselves are still measured and printed, because a
    report a reader can see is not the same thing as a measurement a build fails
    on.
    """
    def rendezvous():
        barrier.wait()  # completes only if a second worker is running too
        return sum(i ** 2 for i in range(20000))

    barrier = threading.Barrier(2, timeout=10)
    with AgentPool(max_workers=2, record_to_pams=False) as pool:
        futures = [pool.submit(rendezvous) for _ in range(2)]
        try:
            totals = sorted(f.result(timeout=30) for f in futures)
        except threading.BrokenBarrierError:  # pragma: no cover - the defect
            raise AssertionError(
                'the pool ran the two tasks one after another: a worker never '
                'started while the other was blocked, so there is no concurrency'
            )
    assert len(totals) == 2

    # Recorded, not asserted: see ADR-015.
    with AgentPool(max_workers=4, record_to_pams=False) as pool:
        results = pool.benchmark(lambda: sum(i ** 2 for i in range(20000)),
                                 worker_levels=[1, 2], iterations_per_level=200)
    print('measured speedup (2 workers vs 1, load-dependent): %.3f'
          % results[2]['speedup'])
    assert results[2]['throughput_per_sec'] > 0


def test_stats_tracking():
    """Test pool stats updated correctly."""
    with AgentPool(max_workers=4, record_to_pams=False) as pool:
        def dummy(x):
            time.sleep(0.001)
            return x
        
        futures = [pool.submit(dummy, i) for i in range(5)]
        for f in futures:
            f.result()
        
        stats = pool.get_stats()
        assert stats['tasks_submitted'] == 5
        assert stats['tasks_completed'] == 5
        assert stats['tasks_failed'] == 0
        assert stats['success_rate'] == '100.0%'


def test_stats_with_errors():
    """Test stats record failures correctly."""
    with AgentPool(max_workers=4, record_to_pams=False) as pool:
        def fail_half(x):
            if x % 2 == 0:
                raise RuntimeError('Even fail')
            return x
        
        futures = [pool.submit(fail_half, i) for i in range(4)]
        for f in futures:
            try:
                f.result()
            except RuntimeError:
                pass
        
        stats = pool.get_stats()
        assert stats['tasks_submitted'] == 4
        assert stats['tasks_completed'] == 2
        assert stats['tasks_failed'] == 2


def test_map_error_handling():
    """Test map handles errors gracefully (stores exception as result)."""
    with AgentPool(max_workers=4, record_to_pams=False) as pool:
        def fail_on_odd(x):
            if x % 2 == 1:
                raise ValueError(f'Odd: {x}')
            return x * 2
        
        results = pool.map(fail_on_odd, range(6))
        assert len(results) == 6
        # Evens should succeed
        assert results[0] == 0
        assert results[2] == 4
        assert results[4] == 8
        # Odds should contain the exception
        assert isinstance(results[1], ValueError)


def test_pool_submit_after_close():
    """Test that submitting after close raises."""
    pool = AgentPool(max_workers=2, record_to_pams=False)
    pool.close()
    try:
        pool.submit(lambda: 1)
        assert False, 'Should have raised'
    except RuntimeError:
        pass


def test_pool_close_idempotent():
    """Test that calling close() twice does not raise."""
    pool = AgentPool(max_workers=2, record_to_pams=False)
    pool.close()
    pool.close()  # Second close should be a no-op
    assert pool.is_active is False


def test_task_history():
    """Test task history tracking."""
    with AgentPool(max_workers=4, record_to_pams=False) as pool:
        for i in range(20):
            pool.submit(lambda x=i: x * 2).result()
        
        history = pool.get_task_history(limit=5)
        assert len(history) == 5
        for t in history:
            assert t.success is True
            assert isinstance(t.task_id, str)


def test_pool_repr():
    """Test pool string representation."""
    with AgentPool(max_workers=2, record_to_pams=False) as pool:
        r = repr(pool)
        assert 'AgentPool' in r
        assert 'workers=2' in r


def test_pool_stats_dataclass():
    """Test PoolStats convenience properties."""
    stats = PoolStats(
        tasks_submitted=10,
        tasks_completed=8,
        tasks_failed=2,
        total_duration_ms=1000.0,
        first_task_at=100.0,
        last_task_at=110.0,
        free_threaded=True,
        max_workers=4,
    )
    assert stats.success_rate == 80.0
    assert stats.avg_duration_ms == 100.0
    assert stats.throughput_per_sec > 0
    d = stats.to_dict()
    assert d['tasks_submitted'] == 10
    assert '80.0%' in d['success_rate']


def test_pool_task_status():
    """Test PoolTask status property."""
    pending = PoolTask(task_id='1', fn_name='test')
    assert pending.status == 'pending'
    
    running = PoolTask(task_id='2', fn_name='test', started_at=time.time())
    assert running.status == 'running'
    
    completed = PoolTask(task_id='3', fn_name='test', completed_at=time.time(), success=True)
    assert completed.status == 'completed'
    
    failed = PoolTask(task_id='4', fn_name='test', error='failed', success=False)
    assert failed.status == 'failed'


class _NullCapture:
    """Stand-in for pytest's capsys so this test can also run under the file's own
    __main__ runner, which has no fixture machinery. pytest still injects capsys and
    the explicit argument wins."""

    class _Out:
        out = "Benchmark"

    def readouterr(self):
        return self._Out()


def test_benchmark_print(capsys=None):
    """Test benchmark_print doesn't crash."""
    if capsys is None:
        capsys = _NullCapture()
    with AgentPool(max_workers=4, record_to_pams=False) as pool:
        results = {1: {'workers': 1, 'duration_sec': 1.0, 'throughput_per_sec': 100.0, 'speedup': 1.0, 'free_threaded': True}}
        pool.benchmark_print(results)
        captured = capsys.readouterr()
        assert 'Benchmark' in captured.out


def test_concurrent_submit_and_map():
    """Test pool handles mixed submit and map operations."""
    with AgentPool(max_workers=4, record_to_pams=False) as pool:
        def add(a, b):
            return a + b
        
        # Submit some tasks
        futures = [pool.submit(add, i, i*2) for i in range(5)]
        submit_results = [f.result() for f in futures]
        
        # Map some tasks
        map_results = pool.map(lambda x: x * 3, range(5))
        
        assert submit_results == [0, 3, 6, 9, 12]
        assert map_results == [0, 3, 6, 9, 12]


if __name__ == '__main__':
    tests = [
        ('pool_creation', test_pool_creation),
        ('pool_context_manager', test_pool_context_manager),
        ('single_submit', test_single_submit),
        ('multiple_submit', test_multiple_submit),
        ('map_basic', test_map_basic),
        ('map_empty', test_map_empty),
        ('submit_error_handling', test_submit_error_handling),
        ('benchmark_basic', test_benchmark_basic),
        ('benchmark_print', test_benchmark_print),
        ('stats_tracking', test_stats_tracking),
        ('stats_with_errors', test_stats_with_errors),
        ('map_error_handling', test_map_error_handling),
        ('pool_submit_after_close', test_pool_submit_after_close),
        ('pool_close_idempotent', test_pool_close_idempotent),
        ('task_history', test_task_history),
        ('pool_repr', test_pool_repr),
        ('pool_stats_dataclass', test_pool_stats_dataclass),
        ('pool_task_status', test_pool_task_status),
        ('concurrent_submit_and_map', test_concurrent_submit_and_map),
    ]
    passed = 0
    failed = 0
    print(f"\n  AgentPool Tests ({len(tests)} tests)")
    print(f"  {'='*50}")
    for name, fn in tests:
        try:
            fn()
            print(f"  [OK] {name}")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")
            failed += 1
    print(f"  {'='*50}")
    print(f"  Passed: {passed}, Failed: {failed}")
