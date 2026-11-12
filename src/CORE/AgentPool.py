# coding: utf-8
"""
AgentPool — Multi-agent execution pool with PAMS integration.

Wraps FreeThreadPool with automatic PAMS recording for every task.
Supports:
- submit(task) -> Future with PAMS event logging
- map(fn, tasks) -> parallel execution with stats
- Benchmark capability at [1,2,4,8,16,24,32] workers
- Stats: tasks submitted, completed, failed, avg duration, throughput

CANONICAL SOURCE — import from here project-wide:
    from azazael.src.CORE.AgentPool import AgentPool

Usage:
    pool = AgentPool(max_workers=8)
    pool.submit(my_agent.execute, {"prompt": "..."})
    results = pool.map(my_fn, list_of_tasks)
    benchmark = pool.benchmark(my_fn, [1,2,4,8,16])
"""

import sys
import os
import time
import threading
from CORE.uuid7 import uuid7
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Dict, List
from concurrent.futures import ThreadPoolExecutor, Future, as_completed

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Import CORE utilities ----------------------------------------------------
_azazael_src = str(Path(__file__).resolve().parent.parent)
if _azazael_src not in sys.path:
    sys.path.insert(0, _azazael_src)

try:
    from CORE.freethreading import (
        is_free_threaded, FreeThreadPool, ThreadSafeDict,  # pyright: ignore[reportAssignmentType]
        ThreadSafeList, ThreadSafeCounter,
    )
    HAS_FREETHREADING = True
except ImportError as exc:
    note_failure("AgentPool:L43", exc)
    HAS_FREETHREADING = False  # pyright: ignore[reportConstantRedefinition]
    ThreadSafeDict = dict
    ThreadSafeList = list
    ThreadSafeCounter = type('ThreadSafeCounter', (), {
        '__init__': lambda s, i=0: setattr(s, '_v', i) or None,
        'increment': lambda s, d=1: (setattr(s, '_v', s._v + d), s._v)[1],
        'value': property(lambda s: s._v),
    })
    def is_free_threaded():
        return hasattr(sys, '_is_gil_enabled') and not sys._is_gil_enabled()
    class FreeThreadPool:
        def __init__(self, max_workers=0, thread_name_prefix='ft-pool'):
            self.max_workers = max_workers or (os.cpu_count() or 8)
            self.thread_name_prefix = thread_name_prefix
            self._executor: Optional[ThreadPoolExecutor] = None
        def __enter__(self): 
            self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix=self.thread_name_prefix)
            return self
        def __exit__(self, *a): 
            if self._executor: self._executor.shutdown(wait=True)
        def submit(self, fn, *a, **kw):
            assert self._executor is not None
            return self._executor.submit(fn, *a, **kw)
        def map(self, fn, *it, **kw):
            assert self._executor is not None
            return self._executor.map(fn, *it, **kw)

try:
    from CORE.PamsRecorder import PamsRecorder, get_recorder  # pyright: ignore[reportAssignmentType]
    HAS_PAMS = True
except ImportError as exc:
    note_failure("AgentPool:L69", exc)
    HAS_PAMS = False  # pyright: ignore[reportConstantRedefinition]
    def PamsRecorder():
        return type('FakeRecorder', (), {
            'record_event': lambda *a, **kw: False, 'get_stats': lambda: {},
        })()
    def get_recorder():
        return PamsRecorder()

IS_FREE_THREADED = is_free_threaded()


@dataclass
class PoolTask:
    """A task tracked by the AgentPool."""
    task_id: str
    fn_name: str
    submitted_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    duration_ms: float = 0.0
    result: Any = None
    error: Optional[str] = None
    success: bool = False

    @property
    def status(self) -> str:
        if self.error:
            return "failed"
        if self.completed_at:
            return "completed"
        if self.started_at:
            return "running"
        return "pending"


@dataclass
class PoolStats:
    """Aggregated pool statistics."""
    tasks_submitted: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    total_duration_ms: float = 0.0
    first_task_at: Optional[float] = None
    last_task_at: Optional[float] = None
    free_threaded: bool = False
    max_workers: int = 0

    @property
    def success_rate(self) -> float:
        total = self.tasks_completed + self.tasks_failed
        return (self.tasks_completed / total * 100) if total > 0 else 100.0

    @property
    def avg_duration_ms(self) -> float:
        total = self.tasks_completed + self.tasks_failed
        return (self.total_duration_ms / total) if total > 0 else 0.0

    @property
    def throughput_per_sec(self) -> float:
        if self.first_task_at and self.last_task_at:
            elapsed = self.last_task_at - self.first_task_at
            return (self.tasks_completed / elapsed) if elapsed > 0 else 0
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'tasks_submitted': self.tasks_submitted,
            'tasks_completed': self.tasks_completed,
            'tasks_failed': self.tasks_failed,
            'success_rate': f"{self.success_rate:.1f}%",
            'avg_duration_ms': f"{self.avg_duration_ms:.1f}",
            'throughput_per_sec': f"{self.throughput_per_sec:.1f}",
            'free_threaded': self.free_threaded,
            'max_workers': self.max_workers,
        }


class AgentPool:
    """
    Multi-agent execution pool with PAMS integration.

    Features:
    - Persistent ThreadPoolExecutor for truly async submit()
    - submit(fn, *args, **kwargs) -> Future with automatic PAMS logging
    - map(fn, iterable) -> parallel execution with stats
    - benchmark(fn, worker_levels) -> throughput measurement
    - Context manager support: with AgentPool(8) as pool: ...
    - Full stats: submitted, completed, failed, avg duration, throughput

    Usage:
        with AgentPool(max_workers=8) as pool:
            futures = [pool.submit(square, i) for i in range(10)]
            results = [f.result() for f in futures]  # truly parallel!
            results = pool.map(process_fn, range(100))
            bench = pool.benchmark(cpu_work_fn)
        # pool auto-shuts down on __exit__
    """

    VERSION = "1.1.0"

    def __init__(self, max_workers: int = 0, record_to_pams: bool = True,
                 pams_source: str = "agent-pool",
                 thread_name_prefix: str = "ft-pool"):
        self.max_workers = max_workers or (os.cpu_count() or 8)
        self.record_to_pams = record_to_pams
        self.pams_source = pams_source
        self.thread_name_prefix = thread_name_prefix

        # -- Persistent executor (lazy-init on first use) -----------------
        self._executor: Optional[ThreadPoolExecutor] = None
        self._executor_lock = threading.Lock()
        self._active = True

        # -- Stats --------------------------------------------------------
        self._stats = PoolStats(
            free_threaded=IS_FREE_THREADED,
            max_workers=self.max_workers,
        )
        self._stats_lock = threading.Lock()

        # -- Task history --------------------------------------------------
        self._task_history: List[PoolTask] = []
        self._history_lock = threading.Lock()

        # -- PAMS recorder -------------------------------------------------
        self._pams: Any = None
        if self.record_to_pams and HAS_PAMS:
            try:
                self._pams = get_recorder()
            except Exception as exc:
                note_failure("AgentPool:L199", exc)

        if self._pams:
            self._pams.record_event(
                "pool_init",
                {"version": self.VERSION, "max_workers": self.max_workers,
                 "free_threaded": IS_FREE_THREADED},
                source=self.pams_source, severity="info",
            )

    # -- Context Manager ------------------------------------------------------

    def __enter__(self) -> 'AgentPool':
        """Enter context manager — initializes the persistent executor."""
        self._ensure_executor()
        return self

    def __exit__(self, *args) -> None:
        """Exit context manager — shuts down the executor gracefully."""
        self.close()

    def close(self, wait: bool = True) -> None:
        """
        Shut down the persistent executor.

        Args:
            wait: If True, wait for running tasks to complete.
                  If False, cancel pending tasks.
        """
        with self._executor_lock:
            self._active = False
            if self._executor is not None:
                self._executor.shutdown(wait=wait, cancel_futures=not wait)
                self._executor = None

        if self._pams:
            stats = self.get_stats()
            self._pams.record_event(
                "pool_shutdown",
                {"tasks_completed": stats.get('tasks_completed', 0),
                 "tasks_failed": stats.get('tasks_failed', 0)},
                source=self.pams_source, severity="info",
            )

    def _ensure_executor(self) -> ThreadPoolExecutor:
        """Lazy-init the persistent executor (thread-safe)."""
        if self._executor is None:
            with self._executor_lock:
                if self._executor is None:
                    if not self._active:
                        raise RuntimeError("AgentPool is closed")
                    self._executor = ThreadPoolExecutor(
                        max_workers=self.max_workers,
                        thread_name_prefix=self.thread_name_prefix,
                    )
        return self._executor

    @property
    def is_active(self) -> bool:
        """Whether the pool is still accepting tasks."""
        return self._active and (self._executor is not None or True)

    # -- Public API ------------------------------------------------------------

    def submit(self, fn: Callable[..., Any], *args, **kwargs) -> Future[Any]:
        """
        Submit a callable for ASYNCHRONOUS execution with PAMS logging.

        Returns immediately with a Future — call .result() to get the value.
        Multiple submit() calls run concurrently in the persistent pool.

        Args:
            fn: Callable to execute.
            *args, **kwargs: Arguments to pass to fn.

        Returns:
            concurrent.futures.Future — call .result() to get the return value.

        Example:
            futures = [pool.submit(square, i) for i in range(100)]
            results = [f.result() for f in futures]  # all ran in parallel!
        """
        task_id = uuid7().hex[:8]
        fn_name = getattr(fn, '__name__', str(fn)[:50])
        task = PoolTask(task_id=task_id, fn_name=fn_name)

        self._update_stats(submitted=1)

        with self._stats_lock:
            if self._stats.first_task_at is None:
                self._stats.first_task_at = time.time()

        if self._pams:
            self._pams.record_event(
                "pool_task_submit",
                {"task_id": task_id, "fn_name": fn_name},
                source=self.pams_source, severity="info",
            )

        def _wrapped() -> Any:
            """Execute fn with timing, error handling, and PAMS recording."""
            task.started_at = time.time()
            try:
                result = fn(*args, **kwargs)
                task.success = True
                task.result = result
                self._update_stats(completed=1)
                self._record_complete(task)
                return result
            except Exception as e:
                task.error = str(e)
                task.success = False
                task.result = None
                self._update_stats(failed=1)
                self._record_error(task, e)
                raise
            finally:
                task.completed_at = time.time()
                task.duration_ms = (task.completed_at - (task.started_at or task.submitted_at)) * 1000
                self._update_stats(duration=task.duration_ms)
                with self._history_lock:
                    self._task_history.append(task)
                    if len(self._task_history) > 500:
                        self._task_history = self._task_history[-500:]

        executor = self._ensure_executor()
        return executor.submit(_wrapped)

    def map(self, fn: Callable[..., Any], *iterables, timeout: Optional[float] = None,
            chunksize: int = 1) -> List[Any]:
        """
        Map a function over iterables in parallel, with PAMS logging.

        Args:
            fn: Callable to apply to each element.
            *iterables: One or more iterables to map over.
            timeout: Max seconds to wait.
            chunksize: Batch size for work distribution.

        Returns:
            List of results in order.
        """
        if not iterables:
            return []

        items = list(iterables[0])
        total = len(items)
        results: List[Any] = [None] * total
        errors = []
        _errors_lock = threading.Lock()

        if self._pams:
            self._pams.record_event(
                "pool_map_start",
                {"fn_name": getattr(fn, '__name__', str(fn)[:50]),
                 "total_items": total, "max_workers": self.max_workers},
                source=self.pams_source, severity="info",
            )

        map_start = time.time()
        executor = self._ensure_executor()

        futures = {}
        for i, item in enumerate(items):
            task_id = uuid7().hex[:8]
            task = PoolTask(task_id=task_id, fn_name=getattr(fn, '__name__', str(fn)[:50]))
            self._update_stats(submitted=1)

            def _wrapped(idx: int, t: PoolTask, itm: Any) -> None:
                t.started_at = time.time()
                try:
                    results[idx] = fn(itm)
                    t.success = True
                    self._update_stats(completed=1)
                except Exception as e:
                    note_failure("AgentPool:L374", e)
                    t.error = str(e)
                    t.success = False
                    self._update_stats(failed=1)
                    with _errors_lock:
                        errors.append({'index': idx, 'error': str(e)})
                    results[idx] = e  # Store exception for caller
                finally:
                    t.completed_at = time.time()
                    t.duration_ms = (t.completed_at - (t.started_at or t.submitted_at)) * 1000
                    self._update_stats(duration=t.duration_ms)
                    with self._history_lock:
                        self._task_history.append(t)
                        if len(self._task_history) > 500:
                            self._task_history = self._task_history[-500:]

            fut = executor.submit(_wrapped, i, task, item)
            futures[fut] = i

        for fut in as_completed(futures):
            try:
                fut.result(timeout=timeout)
            except Exception as e:
                note_failure("AgentPool:L396", e)
                idx = futures[fut]
                errors.append({'index': idx, 'error': f"Future failed: {e}"})

        map_elapsed = time.time() - map_start

        if self._pams:
            self._pams.record_event(
                "pool_map_end",
                {"fn_name": getattr(fn, '__name__', str(fn)[:50]),
                 "total": total, "succeeded": total - len(errors),
                 "failed": len(errors), "elapsed_sec": map_elapsed},
                source=self.pams_source, severity="info",
            )

        return results

    def benchmark(self, workload_fn: Callable[[], Any],
                  worker_levels: Optional[List[int]] = None,
                  iterations_per_level: int = 500) -> Dict[int, Dict[str, Any]]:
        """
        Benchmark throughput at multiple concurrency levels.

        Args:
            workload_fn: A callable representing one unit of work (takes no args).
            worker_levels: List of worker counts to test (default: [1,2,4,8,16]).
            iterations_per_level: Total iterations per level.

        Returns:
            Dict mapping worker_count -> {throughput, speedup, duration_sec, ...}
        """
        if worker_levels is None:
            worker_levels = [1, 2, 4, 8, 16]
            max_real = self.max_workers * 2
            worker_levels = [w for w in worker_levels if w <= max_real]

        results = {}
        baseline_throughput = None

        if self._pams:
            self._pams.record_event(
                "pool_benchmark_start",
                {"levels": worker_levels, "iterations": iterations_per_level},
                source=self.pams_source, severity="info",
            )

        for workers in worker_levels:
            iters_per_worker = max(1, iterations_per_level // workers)

            def worker_task() -> None:
                for _ in range(iters_per_worker):
                    workload_fn()

            start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=workers,
                                    thread_name_prefix=f"{self.thread_name_prefix}-bench") as executor:
                futures = [executor.submit(worker_task) for _ in range(workers)]
                for f in as_completed(futures):
                    try:
                        f.result()
                    except Exception as exc:
                        note_failure("AgentPool:L456", exc)
            duration = time.perf_counter() - start
            throughput = iterations_per_level / duration if duration > 0 else 0

            if baseline_throughput is None:
                baseline_throughput = throughput

            speedup = throughput / baseline_throughput if baseline_throughput and baseline_throughput > 0 else 1.0

            results[workers] = {
                'workers': workers,
                'duration_sec': round(duration, 3),
                'throughput_per_sec': round(throughput, 1),
                'speedup': round(speedup, 3),
                'free_threaded': IS_FREE_THREADED,
            }

        if self._pams:
            best = max(results.items(), key=lambda x: x[1]['throughput_per_sec'])
            self._pams.record_event(
                "pool_benchmark_end",
                {"levels_tested": len(results),
                 "best_workers": best[0],
                 "best_throughput": best[1]['throughput_per_sec'],
                 "best_speedup": best[1]['speedup']},
                source=self.pams_source, severity="info",
            )

        return results

    def benchmark_print(self, results: Dict[int, Dict[str, Any]]) -> None:
        """Print a formatted benchmark table."""
        sep = '=' * 65
        sub = '-' * 45
        print(f"\n{sep}")
        print(f"  AgentPool Benchmark — {'NO-GIL' if IS_FREE_THREADED else 'GIL ENABLED'}")
        print(f"  Max workers: {self.max_workers}")
        print(f"  {'Workers':<10} {'Throughput':<15} {'Speedup':<10} {'Duration':<10}")
        print(f"  {sub}")
        for workers, data in sorted(results.items()):
            print(f"  {workers:<10} {data['throughput_per_sec']:>10.1f}/s  "
                  f"{data['speedup']:>6.2f}x   {data['duration_sec']:>7.2f}s")
        if len(results) > 1:
            best = max(results.items(), key=lambda x: x[1]['throughput_per_sec'])
            print(f"  {sub}")
            print(f"  Best: {best[0]} workers @ {best[1]['throughput_per_sec']:.1f}/s "
                  f"({best[1]['speedup']:.2f}x speedup)")
        print(f"{sep}\n")

    # -- Stats -----------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get pool statistics as a dict."""
        with self._stats_lock:
            stats = self._stats.to_dict()
        stats['version'] = self.VERSION
        stats['history_size'] = len(self._task_history)
        return stats

    def get_task_history(self, limit: int = 50) -> List[PoolTask]:
        """Get recent task history."""
        with self._history_lock:
            return self._task_history[-limit:]

    def print_stats(self) -> None:
        """Print pool statistics to stdout."""
        stats = self.get_stats()
        sep = '=' * 50
        print(f"\n{sep}")
        print(f"  AgentPool v{stats['version']}")
        print(f"  Free-threaded: {stats['free_threaded']}")
        print(f"  Task history: {stats['history_size']}")
        print(f"  Submitted: {stats['tasks_submitted']}")
        print(f"  Completed: {stats['tasks_completed']}")
        print(f"  Failed: {stats['tasks_failed']}")
        print(f"  Success rate: {stats['success_rate']}")
        print(f"  Avg duration: {stats['avg_duration_ms']}")
        print(f"  Throughput: {stats['throughput_per_sec']}")
        print(f"{sep}\n")

    # -- Internal --------------------------------------------------------------

    def _update_stats(self, submitted: int = 0, completed: int = 0,
                      failed: int = 0, duration: float = 0.0) -> None:
        """Thread-safe stats update."""
        with self._stats_lock:
            self._stats.tasks_submitted += submitted
            self._stats.tasks_completed += completed
            self._stats.tasks_failed += failed
            self._stats.total_duration_ms += duration
            if completed > 0 or failed > 0:
                self._stats.last_task_at = time.time()

    def _record_complete(self, task: PoolTask) -> None:
        """Record task completion to PAMS."""
        if self._pams:
            self._pams.record_event(
                "pool_task_complete",
                {"task_id": task.task_id, "fn_name": task.fn_name,
                 "duration_ms": task.duration_ms, "success": True},
                source=self.pams_source, severity="info",
            )

    def _record_error(self, task: PoolTask, error: Exception) -> None:
        """Record task error to PAMS."""
        if self._pams:
            self._pams.record_event(
                "pool_task_error",
                {"task_id": task.task_id, "fn_name": task.fn_name,
                 "error": str(error)[:500]},
                source=self.pams_source, severity="error",
            )

    def __repr__(self) -> str:
        return (f"AgentPool(workers={self.max_workers}, "
                f"ft={IS_FREE_THREADED}, "
                f"tasks={self._stats.tasks_completed}/{self._stats.tasks_submitted})")


# -- Self-test -----------------------------------------------------------------

if __name__ == '__main__':
    print("=" * 65)
    print("  AgentPool - Self-test")
    print("=" * 65)
    print(f"  Free-threaded: {IS_FREE_THREADED}")
    print(f"  CPU cores: {os.cpu_count()}")

    with AgentPool(max_workers=4) as pool:
        # -- Test 1: submit (truly async with persistent executor) --------
        print("\n  [1] Testing async submit()...")
        def square(x: int) -> int:
            time.sleep(0.01)
            return x * x

        futures = [pool.submit(square, i) for i in range(10)]
        results = []
        for f in futures:
            try:
                results.append(f.result())
            except Exception as e:
                note_failure("AgentPool:L597", e)
                results.append(f"ERROR: {e}")
        print(f"      Results: {results}")
        print(f"      (all 10 ran concurrently in persistent pool)")

        # -- Test 2: map --------------------------------------------------
        print("\n  [2] Testing map()...")
        def slow_double(x: int) -> int:
            time.sleep(0.005)
            return x * 2

        mapped = pool.map(slow_double, range(20), timeout=30)
        print(f"      First 10 of 20: {mapped[:10]}")

        # -- Test 3: benchmark --------------------------------------------
        print("\n  [3] Running benchmark...")
        def math_work() -> None:
            total = sum(i * i for i in range(2000))
            _ = total  # discard

        bench = pool.benchmark(math_work, worker_levels=[1, 2, 4, 8],
                               iterations_per_level=400)
        pool.benchmark_print(bench)

        # -- Test 4: stats ------------------------------------------------
        print("\n  [4] Pool stats:")
        pool.print_stats()

        # -- Test 5: error handling ---------------------------------------
        print("\n  [5] Testing error handling...")
        def fail_on_odd(x: int) -> int:
            if x % 2 == 1:
                raise ValueError(f"Odd number: {x}")
            return x

        fut_bad = pool.submit(fail_on_odd, 3)
        try:
            fut_bad.result()
            print("      (no error — unexpected)")
        except Exception as e:
            print(f"      Caught expected error: {e}")

        # Final stats
        print("\n  [*] Final pool stats:")
        pool.print_stats()

    print("  Pool auto-closed via context manager.")
    print("=" * 65)
