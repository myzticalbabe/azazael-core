# coding: utf-8
"""
FREETHREAD_UPGRADE.py — Python 3.14t Free-Threading Integration Layer

Provides drop-in ThreadPool wrappers for all worm executors and cluster runners.
Automatically detects free-threading and selects optimal pool implementation.

Key abstractions:
  - WormPool:       Per-worm parallel executor with correct worker sizing
  - ClusterRunner:  Multi-worm batch runner with diverse scheduling
  - FreeThreadPool: Re-exported from CORE.freethreading for convenience

CANONICAL SOURCE — import from here:
    from CORE.FREETHREAD_UPGRADE import WormPool, ClusterRunner, upgrade_worm_executor
"""

import os
import sys
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from datetime import datetime
import sysconfig

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Import Free-Threading Core ----------------------------------------------
try:
    from CORE.freethreading import (
        is_free_threaded,
        FreeThreadPool,
        ThreadSafeDict,
        ThreadSafeList,
        DeviceParallelConfig,
    )
except ImportError:
    # Fallback for standalone usage
    def is_free_threaded() -> bool:
        try:
            return bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
        except Exception as exc:
            note_failure("FREETHREAD_UPGRADE:L41", exc)
            return False

    class _FreeThreadPool:
        # Fallback pool; aliases the canonical name below so callers of this
        # module always see `FreeThreadPool` regardless of which branch ran.
        def __init__(self, max_workers=0, thread_name_prefix="ft-worker"):
            if not isinstance(max_workers, int):  # type: ignore[reportUnnecessaryIsInstance]
                raise TypeError(f"max_workers must be int, got {type(max_workers).__name__}")
            self.max_workers = max_workers if max_workers > 0 else (os.cpu_count() or 8)
            self.thread_name_prefix = thread_name_prefix
            self.executor = None

        def __enter__(self):
            self.executor = ThreadPoolExecutor(
                max_workers=self.max_workers, 
                thread_name_prefix=self.thread_name_prefix
            )
            return self

        def __exit__(self, *a):
            if self.executor:
                self.executor.shutdown(wait=True, cancel_futures=True)
                self.executor = None

        def submit(self, fn, *a, **kw):
            if self.executor is None:
                raise RuntimeError("FreeThreadPool not entered — use 'with'")
            return self.executor.submit(fn, *a, **kw)

        def map(self, fn, *it, **kw):
            if self.executor is None:
                raise RuntimeError("FreeThreadPool not entered — use 'with'")
            return self.executor.map(fn, *it, **kw)

    FreeThreadPool = _FreeThreadPool
    ThreadSafeDict = dict
    ThreadSafeList = list

    @dataclass
    class _DeviceParallelConfig:
        device_name: str = "CPU"
        max_workers: int = 16
        optimal_workers: int = 16
        free_threaded_speedup: float = 1.0
        gil_limited: bool = True

        @classmethod
        def for_cpu(cls):
            return cls(max_workers=os.cpu_count() or 16, optimal_workers=os.cpu_count() or 16)
        
        @classmethod
        def for_device(cls, d):
            return cls.for_cpu()

    DeviceParallelConfig = _DeviceParallelConfig


# ===========================================================================
#  WormPool — Per-Worm Parallel Executor
# ===========================================================================

@dataclass
class TaskResult:
    """Result of a single task submitted to WormPool."""
    task_id: str
    worm_name: str
    success: bool
    result: Any = None
    error: str = ""
    elapsed_ms: float = 0.0

@dataclass
class WormPool:
    """Parallel executor for a single worm's operations.

    Auto-sizes concurrency for the detected Python environment:
      - Python 3.14t (NOGIL): cpu_count workers for CPU-bound tasks
      - Python <3.13 (GIL):   cpu_count/2 for I/O-bound, 1 for CPU-bound

    Usage:
        with WormPool("SYSTEMZ", task_type="io") as pool:
            results = pool.map(systemz.poll_service, service_names)

    Replaces the existing WormExecutor pattern in CONCURRENCY.py and
    CLUSTERWORMS.py with a 3.14t-aware alternative.
    """

    worm_name: str
    max_workers: int = 0
    task_type: str = "io"  # "io", "cpu", "mixed"
    _results: List[TaskResult] = field(default_factory=list, repr=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, init=False)
    # The pooled executor is NOT a dataclass field: the concrete pool class is
    # chosen at module import time (canonical re-export vs GIL fallback) and is
    # created lazily in __enter__, so it lives on a plain instance attribute.

    def __post_init__(self):
        self._pool = None
        ft = is_free_threaded()
        cpu = os.cpu_count() or 8
        if not isinstance(self.max_workers, int):  # type: ignore[reportUnnecessaryIsInstance]
            raise TypeError(f"max_workers must be int, got {type(self.max_workers).__name__}")
        if self.max_workers < 0:
            raise ValueError(f"max_workers must be >= 0, got {self.max_workers}")
        if self.max_workers > 1024:
            import warnings
            warnings.warn(f"WormPool max_workers={self.max_workers} capped to 1024")
            self.max_workers = 1024
        if self.max_workers == 0:
            if ft:
                self.max_workers = cpu if self.task_type == "cpu" else min(32, cpu * 4)
            else:
                self.max_workers = max(1, cpu // 2) if self.task_type == "io" else 1

    def __enter__(self) -> "WormPool":
        self._pool = FreeThreadPool(
            max_workers=self.max_workers,
            thread_name_prefix=f"worm-{self.worm_name.lower()}",
        )
        self._pool.__enter__()
        return self

    def __exit__(self, *args):
        if self._pool:
            self._pool.__exit__(*args)
            self._pool = None

    def submit(self, fn: Callable, *args, **kwargs) -> Future:
        """Submit a single task and wrap in a Future."""
        if self._pool is None:
            raise RuntimeError("WormPool not entered — use 'with WormPool(...) as pool:'")
        return self._pool.submit(fn, *args, **kwargs)

    def map(self, fn: Callable, items: List[Any], *extra_args, **extra_kwargs) -> List[TaskResult]:
        """Map a function over items with parallel execution tracking."""
        if self._pool is None:
            raise RuntimeError("WormPool not entered")

        results: List[TaskResult] = [None] * len(items)  # type: ignore

        def worker(idx: int, item: Any):
            start = time.perf_counter()
            try:
                res = fn(item, *extra_args, **extra_kwargs)
                task = TaskResult(
                    task_id=f"{self.worm_name}-{idx}",
                    worm_name=self.worm_name,
                    success=True,
                    result=res,
                    elapsed_ms=round((time.perf_counter() - start) * 1000, 2),
                )
            except Exception as e:
                note_failure("FREETHREAD_UPGRADE:L185", e)
                task = TaskResult(
                    task_id=f"{self.worm_name}-{idx}",
                    worm_name=self.worm_name,
                    success=False,
                    error=str(e),
                    elapsed_ms=round((time.perf_counter() - start) * 1000, 2),
                )
            results[idx] = task

        futures = [self._pool.submit(worker, i, item) for i, item in enumerate(items)]
        for f in as_completed(futures):
            f.result()  # Raise on exception

        with self._lock:
            self._results.extend([r for r in results if r is not None])
        return [r for r in results if r is not None]

    def get_stats(self) -> Dict[str, Any]:
        """Get aggregated stats from this pool's runs."""
        with self._lock:
            if not self._results:
                return {"worm": self.worm_name, "tasks": 0}
            successes = [r for r in self._results if r.success]
            elapsed = [r.elapsed_ms for r in self._results]
            return {
                "worm": self.worm_name,
                "tasks": len(self._results),
                "success_rate": round(len(successes) / len(self._results) * 100, 1),
                "avg_ms": round(sum(elapsed) / len(elapsed), 2),
                "max_ms": max(elapsed),
                "min_ms": min(elapsed),
                "free_threaded": is_free_threaded(),
                "max_workers_used": self.max_workers,
            }


# ===========================================================================
#  ClusterRunner — Multi-Worm Batch Runner
# ===========================================================================

@dataclass
class ClusterRunResult:
    """Result of a cluster run with multiple worms."""
    run_id: str
    worm_results: Dict[str, List[TaskResult]] = field(default_factory=dict)
    start_time: float = 0.0
    end_time: float = 0.0
    total_duration_sec: float = 0.0
    worms_executed: int = 0
    total_tasks: int = 0
    free_threaded: bool = False

@dataclass
class ClusterRunner:
    """Multi-worm batch runner with 3.14t-optimized scheduling.

    Runs multiple worm pools concurrently, each with its own worker configuration.
    Under 3.14t, all worm pools truly run in parallel.
    Under GIL, pools alternate in a time-sliced fashion.

    Usage:
        with ClusterRunner(["SYSTEMZ", "PROCESSZ", "NETWORXZ"]) as runner:
            runner.add_worm_tasks("SYSTEMZ", worm1_poll_services, service_list)
            runner.add_worm_tasks("PROCESSZ", worm2_scan_processes, pid_list)
            result = runner.execute()

    Replaces the ClusterExecutor pattern in CONCURRENCY.py.
    """

    worm_names: List[str]
    _pools: Dict[str, WormPool] = field(default_factory=dict, repr=False, init=False)
    _task_queue: Dict[str, Tuple[Callable, List[Any]]] = field(default_factory=dict, repr=False, init=False)
    _entered: bool = field(default=False, repr=False, init=False)

    def __enter__(self) -> "ClusterRunner":
        for name in self.worm_names:
            self._pools[name] = WormPool(name).__enter__()
        self._entered = True
        return self

    def __exit__(self, *args):
        for pool in self._pools.values():
            pool.__exit__(*args)
        self._pools.clear()
        self._entered = False

    def add_worm_tasks(self, worm_name: str, fn: Callable, items: List[Any]):
        """Queue tasks for a specific worm."""
        if worm_name not in self._pools:
            raise ValueError(f"Worm '{worm_name}' not in cluster. Registered: {list(self._pools)}")
        self._task_queue[worm_name] = (fn, items)

    def execute(self) -> ClusterRunResult:
        """Execute all queued worm tasks in parallel using individual pools."""
        if not self._entered:
            raise RuntimeError("ClusterRunner not entered. Use 'with ClusterRunner(...) as runner:'")

        start = time.perf_counter()
        run_id = f"cluster-{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Submit all tasks and track futures with metadata
        all_futures: Dict[str, List[Tuple[Future, str, float]]] = {}
        for worm_name, (fn, items) in self._task_queue.items():
            pool = self._pools[worm_name]
            tracked = []
            for i, item in enumerate(items):
                task_start = time.perf_counter()
                fut = pool.submit(fn, item)
                tracked.append((fut, f"{worm_name}-{i}", task_start))
            all_futures[worm_name] = tracked

        # Wait for all and collect results per-worm
        worm_results: Dict[str, List[TaskResult]] = {}
        total_tasks = 0
        for worm_name, tracked_futs in all_futures.items():
            results: List[TaskResult] = []
            for fut, task_id, task_start in tracked_futs:
                try:
                    result = fut.result()
                    results.append(TaskResult(
                        task_id=task_id, worm_name=worm_name, success=True,
                        result=result,
                        elapsed_ms=round((time.perf_counter() - task_start) * 1000, 2),
                    ))
                except Exception as e:
                    note_failure("FREETHREAD_UPGRADE:L310", e)
                    results.append(TaskResult(
                        task_id=task_id, worm_name=worm_name, success=False,
                        error=str(e),
                        elapsed_ms=round((time.perf_counter() - task_start) * 1000, 2),
                    ))
                total_tasks += 1
            worm_results[worm_name] = results

        end = time.perf_counter()
        return ClusterRunResult(
            run_id=run_id,
            worm_results=worm_results,
            start_time=start,
            end_time=end,
            total_duration_sec=round(end - start, 3),
            worms_executed=len(self._task_queue),
            total_tasks=total_tasks,
            free_threaded=is_free_threaded(),
        )


# ===========================================================================
#  Upgrade Helper — Patch Existing Worms for 3.14t
# ===========================================================================

def upgrade_worm_executor(worm_instance: Any) -> Any:
    """Upgrade an existing worm's internal executor to use FreeThreadPool.

    Detects if the worm has a self.executor, self._thread_pool, or similar
    attribute, and replaces it with a WormPool-wrapped FreeThreadPool.

    Works on:
      - ConcurrencyManager (CONCURRENCY.py)
      - ClusterWORMZ (CLUSTERWORMS.py)
      - WORMZ (WORMZ.py)
      - Any class with ThreadPoolExecutor-like attributes

    Returns the worm_instance for chaining.
    """
    worm_name = getattr(worm_instance, "__class__", type(worm_instance)).__name__
    ft = is_free_threaded()

    # Check for common executor patterns
    executor_attrs = ["executor", "_executor", "thread_pool", "_pool", "_threadpool", "pool"]
    for attr in executor_attrs:
        if hasattr(worm_instance, attr):
            old = getattr(worm_instance, attr)
            if isinstance(old, ThreadPoolExecutor):
                old.shutdown(wait=True)
            new_pool = FreeThreadPool(
                max_workers=(os.cpu_count() or 8) if ft else max(1, (os.cpu_count() or 8) // 2),
                thread_name_prefix=f"upgraded-{worm_name.lower()}",
            )
            setattr(worm_instance, attr, new_pool)
            break

    # Set free-threading flag for introspection
    setattr(worm_instance, "_free_threaded", ft)
    return worm_instance

def batch_upgrade_worms(worm_instances: List[Any]) -> List[Any]:
    """Upgrade a list of worm instances for 3.14t."""
    return [upgrade_worm_executor(w) for w in worm_instances]


# ===========================================================================
#  Environment Diagnostic
# ===========================================================================

def print_upgrade_info():
    """Print 3.14t upgrade diagnostics."""
    ft = is_free_threaded()
    cpu = os.cpu_count() or 8
    py_ver = sys.version.split()[0]
    print("=" * 60)
    print("  FREETHREADING UPGRADE LAYER — Diagnostics")
    print("=" * 60)
    print(f"  Python version:  {py_ver}")
    print(f"  Free-threaded:   {ft}")
    print(f"  CPU cores:       {cpu}")
    print(f"  Effective parallel: {cpu if ft else 1}")
    print()
    print("  Recommended pool sizes:")
    for t in ["io", "cpu", "mixed"]:
        pool = WormPool("_test", task_type=t)
        print(f"    {t:6s}: {pool.max_workers:2d} workers")
    print("=" * 60)


if __name__ == "__main__":
    print_upgrade_info()
