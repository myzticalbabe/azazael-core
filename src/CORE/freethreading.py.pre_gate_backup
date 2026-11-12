"""
freethreading.py — Centralized free-threading utilities for Python 3.14t.

Provides thread-safe data structures, device-aware parallelism helpers,
OpenVINO async inference wrappers, and performance benchmarking tools.

CANONICAL SOURCE — import from here project-wide:
    from azazael.src.CORE.freethreading import (
        is_free_threaded, get_thread_info, FreeThreadPool,
        DeviceParallelConfig, OpenVINOAsyncRunner, benchmark_parallel,
        WorkStealingQueue, WorkStealingPool,
    )

Original location (deprecated): llama-server/unified/freethread_utils.py
"""

import os
import sys
import time
import threading
import ctypes
import sysconfig
from collections import deque
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

# -- Cancellation Token --------------------------------------------------


class TaskCancelledError(Exception):
    """Raised when a task is interrupted via cooperative cancellation."""


class CancellationToken:
    """Thread-safe cooperative cancellation token for Python 3.14t free-threading.

    Uses threading.Event internally for lock-free signalling (safe under
    no-GIL free-threading). Supports optional cleanup callbacks and
    cooperative cancellation checks via raise_if_cancelled().

    Usage:
        token = CancellationToken()
        # In worker thread:
        token.raise_if_cancelled()  # raises TaskCancelledError if signalled
        if token.is_cancelled:      # boolean check
            return
        # In controller:
        token.cancel()              # signals all workers, fires callbacks
    """

    def __init__(self):
        self._event = threading.Event()
        self._callbacks: list = []
        self._lock = threading.Lock()

    def cancel(self) -> None:
        """Signal cancellation and trigger all registered callbacks.

        Idempotent — subsequent calls are no-ops. Callbacks run in the
        calling thread's context and exceptions are silently caught.
        """
        self._event.set()
        with self._lock:
            callbacks = list(self._callbacks)
            self._callbacks.clear()
        for cb in callbacks:
            try:
                cb()
            except Exception:
                pass

    @property
    def is_cancelled(self) -> bool:
        """Check if cancellation has been signalled (non-blocking)."""
        return self._event.is_set()

    def wait(self, timeout: float = None) -> bool:
        """Block until cancelled or timeout expires. Returns True if cancelled."""
        return self._event.wait(timeout)

    def raise_if_cancelled(self) -> None:
        """Raise TaskCancelledError if the token has been signalled.

        Worker threads call this periodically to cooperatively bail out.
        """
        if self._event.is_set():
            raise TaskCancelledError("Task execution cancelled via token")

    def register_callback(self, cb: callable) -> None:
        """Register a cleanup callback. Fires immediately if already cancelled."""
        with self._lock:
            if self._event.is_set():
                try:
                    cb()
                except Exception:
                    pass
            else:
                self._callbacks.append(cb)

    def reset(self) -> None:
        """Reset the token for reuse (clears signal + callbacks)."""
        self._event.clear()
        with self._lock:
            self._callbacks.clear()

    def __repr__(self) -> str:
        return f"CancellationToken(cancelled={self.is_cancelled})"


# -- Free-Threading Detection --------------------------------------------------

def is_free_threaded() -> bool:
    """Dynamically check if Python is running with GIL disabled (Py_GIL_DISABLED=1).
    
    Uses sysconfig at runtime instead of hardcoding True.
    """
    try:
        return bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
    except Exception:
        # Fallback for older Python or unusual builds
        return hasattr(sys, '_is_gil_enabled') and not sys._is_gil_enabled()

def get_thread_info() -> dict:
    """Get comprehensive free-threading and threading diagnostic info."""
    return {
        'free_threaded': is_free_threaded(),
        'python_version': sys.version,
        'python_executable': sys.executable,
        'cpu_count': os.cpu_count(),
        'threading_active_count': threading.active_count(),
        'threading_main_thread': threading.main_thread().name,
        'gil_status': 'disabled' if is_free_threaded() else 'enabled',
        'effective_parallelism': os.cpu_count() if is_free_threaded() else 1,
        'sys_path_preview': sys.path[:3] if len(sys.path) > 3 else sys.path,
    }

def print_thread_info() -> None:
    """Print human-readable free-threading diagnostic info."""
    info = get_thread_info()
    ft = info['free_threaded']
    print(f"  [{'NO-GIL' if ft else 'GIL ENABLED'}] Python {info['python_version'].split()[0]}")
    print(f"  Executable: {info['python_executable']}")
    print(f"  CPU cores: {info['cpu_count']} | Active threads: {info['threading_active_count']}")
    print(f"  Effective parallelism: {info['effective_parallelism']} ({'GIL disabled' if ft else 'GIL serialized'})")

# -- Thread-Safe Data Structures -----------------------------------------------

T = TypeVar('T')

class ThreadSafeCounter:
    """Atomic counter for thread-safe increment/decrement operations."""

    def __init__(self, initial: int = 0):
        self._value = initial
        self._lock = threading.Lock()
        
    def increment(self, delta: int = 1) -> int:
        with self._lock:
            self._value += delta
            return self._value

    def decrement(self, delta: int = 1) -> int:
        with self._lock:
            self._value -= delta
            return self._value

    @property
    def value(self) -> int:
        with self._lock:
            return self._value

    def reset(self) -> None:
        with self._lock:
            self._value = 0

    def __repr__(self) -> str:
        return f"ThreadSafeCounter({self.value})"


class ThreadSafeDict(dict):
    """Thread-safe dictionary with per-operation locking on all mutations and reads."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = threading.Lock()

    def __setitem__(self, key, value):
        with self._lock:
            super().__setitem__(key, value)

    def __getitem__(self, key):
        with self._lock:
            return super().__getitem__(key)

    def __delitem__(self, key):
        with self._lock:
            super().__delitem__(key)

    def __contains__(self, key):
        with self._lock:
            return super().__contains__(key)

    def __iter__(self):
        with self._lock:
            return iter(list(super().__iter__()))

    def __len__(self):
        with self._lock:
            return super().__len__()

    def get_and_update(self, key: str, default: Any = None, updater: Optional[Callable] = None) -> Any:
        """Atomically get value and optionally update."""
        with self._lock:
            val = super().get(key, default)
            if updater:
                super().__setitem__(key, updater(val))
            return val

    def get(self, key, default=None):
        with self._lock:
            return super().get(key, default)

    def safe_get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return super().get(key, default)

    def keys_safe(self) -> list:
        with self._lock:
            return list(super().keys())

    def values_safe(self) -> list:
        with self._lock:
            return list(super().values())


class ThreadSafeList:
    """Thread-safe list with locked access for all common operations."""

    def __init__(self, initial: list | None = None):
        self._items = list(initial) if initial else []
        self._lock = threading.Lock()

    def __getitem__(self, index):
        with self._lock:
            return self._items[index]

    def __setitem__(self, index, value):
        with self._lock:
            self._items[index] = value

    def append(self, item: Any) -> None:
        with self._lock:
            self._items.append(item)

    def extend(self, items: list) -> None:
        with self._lock:
            self._items.extend(items)

    def get_all(self) -> list:
        with self._lock:
            return list(self._items)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def __contains__(self, item):
        with self._lock:
            return item in self._items

    def __iter__(self):
        with self._lock:
            return iter(list(self._items))

    def __repr__(self) -> str:
        with self._lock:
            return f"ThreadSafeList({len(self._items)} items)"


# -- Enhanced ThreadPool -------------------------------------------------------

@dataclass
class FreeThreadPool:
    """ThreadPoolExecutor auto-sized for free-threading (no-GIL) parallelism."""

    max_workers: int = 0
    thread_name_prefix: str = "ft-worker"
    _executor: ThreadPoolExecutor | None = field(default=None, repr=False, init=False)

    def __post_init__(self):
        # Input validation (per inference model finding)
        if not isinstance(self.max_workers, int):  # type: ignore[reportUnnecessaryIsInstance]
            raise TypeError(f"max_workers must be int, got {type(self.max_workers).__name__}")
        if self.max_workers < 0:
            raise ValueError(f"max_workers must be >= 0, got {self.max_workers}")
        if self.max_workers > 1024:
            import warnings
            warnings.warn(
                f"max_workers={self.max_workers} exceeds recommended cap of 1024 — "
                f"capping to 1024. Set max_workers <= 1024 explicitly to silence this warning."
            )
            self.max_workers = 1024
        if not isinstance(self.thread_name_prefix, str):  # type: ignore[reportUnnecessaryIsInstance]
            raise TypeError(f"thread_name_prefix must be str, got {type(self.thread_name_prefix).__name__}")
        if not self.thread_name_prefix.strip():
            raise ValueError("thread_name_prefix must not be empty")
        if self.max_workers == 0:
            self.max_workers = os.cpu_count() or 8
        self._executor = None

    def __enter__(self) -> 'FreeThreadPool':
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix=self.thread_name_prefix,
        )
        return self

    def __exit__(self, *args):
        if self._executor:
            # Use wait=False to avoid hanging on worker threads that get stuck
            # under Python 3.14t free-threading (e.g., import-system deadlocks).
            # The caller is responsible for waiting on futures via as_completed().
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    @property
    def executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            raise RuntimeError("FreeThreadPool not entered — use 'with FreeThreadPool() as pool:'")
        return self._executor

    def submit(self, fn, *args, **kwargs) -> Future:
        return self.executor.submit(fn, *args, **kwargs)

    def map(self, fn, *iterables, timeout=None, chunksize=1):
        return self.executor.map(fn, *iterables, timeout=timeout, chunksize=chunksize)

    def submit_all(self, tasks: List[Tuple[Callable, Tuple, Dict]]) -> Dict[Future, Any]:
        """Submit multiple tasks at once. Returns {future: label} mapping.

        Tasks format: [(fn, args_tuple, kwargs_dict), ...]
        Labels default to incrementing integers if not provided via kwargs['_label'].

        NOTE: Each task's kwargs dict is shallow-copied before _label extraction
        to avoid mutating the caller's original dicts.
        """
        futures: Dict[Future, Any] = {}
        for i, (fn, args, kwargs) in enumerate(tasks):
            # Shallow-copy to avoid mutating the caller's kwargs dict
            if isinstance(kwargs, dict):  # type: ignore[reportUnnecessaryIsInstance]
                kwargs = dict(kwargs)
                label = kwargs.pop('_label', i)
            else:
                label = i
                kwargs = {}
            future = self.submit(fn, *args, **kwargs)
            futures[future] = label
        return futures

    def collect_results(self, futures: Dict[Future, Any], timeout: Optional[float] = None) -> List[Dict[str, Any]]:
        """Collect results from submitted futures via as_completed.

        Returns list of {"label": ..., "success": ..., "result": ..., "error": ...}.
        Single-threaded collection — no lock needed on results list.
        """
        results: List[Dict[str, Any]] = []
        try:
            for future in as_completed(futures, timeout=timeout):
                label = futures[future]
                try:
                    results.append({
                        "label": label, "success": True,
                        "result": future.result(timeout=1.0), "error": None,
                    })
                except TimeoutError:
                    future.cancel()
                    results.append({
                        "label": label, "success": False,
                        "result": None, "error": "timeout",
                    })
                except Exception as e:
                    results.append({
                        "label": label, "success": False,
                        "result": None, "error": str(e),
                    })
        except TimeoutError:
            for future, label in futures.items():
                if not future.done():
                    future.cancel()
                    results.append({
                        "label": label, "success": False,
                        "result": None, "error": "pool-level timeout",
                    })
        return results

    @staticmethod
    def adaptive_workers(num_tasks: int, io_bound: bool = False) -> int:
        """Compute optimal worker count for a workload.

        For CPU-bound workloads: min(num_tasks, cpu_count).
        For I/O-bound workloads: min(num_tasks, cpu_count * 4), capped at 32.

        This avoids over-subscription (100 tasks -> 100 threads) while
        providing enough parallelism for I/O-heavy workloads like worm
        execution that are dominated by network/disk wait.
        """
        cpu = os.cpu_count() or 8
        if io_bound:
            return min(num_tasks, min(32, cpu * 4))
        return min(num_tasks, cpu)

    @staticmethod
    def info() -> dict:
        cpu = os.cpu_count() or 8
        return {
            'free_threaded': is_free_threaded(),
            'cpu_count': cpu,
            'recommended_workers': cpu,
            'max_workers_for_io': min(32, cpu * 4),
            'max_workers_for_cpu': cpu,
            'max_workers_for_gpu': cpu // 2,
        }


# -- Work-Stealing Queue & Pool ------------------------------------------------

@dataclass
class WorkStealingQueue:
    """Thread-safe double-ended queue for work-stealing.

    Workers push/pop from their own end (LIFO for cache locality).
    Idle workers steal from the opposite end (FIFO) of other workers' queues.
    This minimizes lock contention while enabling dynamic load balancing.

    Python 3.14t free-threading safe — uses threading.Lock for all operations.
    """

    _items: deque = field(default_factory=deque)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def push(self, item: Any) -> None:
        """Push to own queue (LIFO — fast, cache-local)."""
        with self._lock:
            self._items.append(item)

    def pop(self) -> Optional[Any]:
        """Pop from own queue (LIFO)."""
        with self._lock:
            return self._items.pop() if self._items else None

    def steal(self) -> Optional[Any]:
        """Steal from the front of the queue (FIFO — largest granularity).

        Stealing from the front minimizes contention since the owner
        pops from the back. Uses deque.popleft() for O(1) amortized
        instead of list.pop(0) which is O(N).
        """
        with self._lock:
            if not self._items:
                return None
            return self._items.popleft()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._items)

    @property
    def is_empty(self) -> bool:
        with self._lock:
            return len(self._items) == 0

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __repr__(self) -> str:
        return f"WorkStealingQueue({self.size} items)"


@dataclass
class WorkStealingPool:
    """Thread pool with work-stealing for dynamic load balancing.

    Each worker has its own WorkStealingQueue. When a worker finishes
    its local work, it attempts to steal from other workers' queues.
    This provides automatic load balancing without central coordination.

    Usage:
        with WorkStealingPool(num_workers=8) as pool:
            # Submit all tasks
            futures = [pool.submit(task_fn, arg) for arg in args]
            # Collect results
            results = pool.results()
    """

    num_workers: int = 0
    thread_name_prefix: str = "ws-worker"
    steal_attempts: int = 3

    # Internal state — initialized in __post_init__ / __enter__
    _queues: List[WorkStealingQueue] = field(default_factory=list, init=False, repr=False)
    _workers: List[threading.Thread] = field(default_factory=list, init=False, repr=False)
    _running: bool = field(default=False, init=False, repr=False)
    _shutdown_event: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _results: List[Dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _results_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _total_submitted: int = field(default=0, init=False, repr=False)
    _total_completed: int = field(default=0, init=False, repr=False)
    _counter_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _steals_succeeded: int = field(default=0, init=False, repr=False)
    _steals_failed: int = field(default=0, init=False, repr=False)
    _steal_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self):
        if self.num_workers <= 0:
            self.num_workers = os.cpu_count() or 8

    def __enter__(self) -> 'WorkStealingPool':
        self._shutdown_event.clear()
        self._running = True
        self._queues = [WorkStealingQueue() for _ in range(self.num_workers)]
        self._workers = []
        self._results = []
        self._total_submitted = 0
        self._total_completed = 0
        self._steals_succeeded = 0
        self._steals_failed = 0

        for i in range(self.num_workers):
            t = threading.Thread(
                target=self._worker_loop,
                args=(i,),
                name=f"{self.thread_name_prefix}-{i}",
            )
            t.daemon = True
            self._workers.append(t)
            t.start()

        return self

    def __exit__(self, *args):
        self._running = False
        self._shutdown_event.set()
        for t in self._workers:
            t.join(timeout=5.0)
        self._queues.clear()
        self._workers.clear()

    # -- Submit --------------------------------------------------

    def submit(self, fn: Callable, *args, **kwargs) -> int:
        """Submit a task to the pool.

        Tasks are distributed round-robin across worker queues.
        Returns a task ID for result lookup.
        """
        task_id: int = 0
        with self._counter_lock:
            task_id = self._total_submitted
            self._total_submitted += 1

        target_queue = task_id % self.num_workers
        self._queues[target_queue].push({
            "task_id": task_id,
            "fn": fn,
            "args": args,
            "kwargs": kwargs,
        })
        return task_id

    def submit_all(self, tasks: List[Tuple[Callable, tuple, dict]]) -> List[int]:
        """Submit multiple tasks at once. Returns list of task IDs."""
        return [self.submit(fn, *args, **kwargs) for fn, args, kwargs in tasks]

    # -- Results -------------------------------------------------

    def results(self) -> List[Dict[str, Any]]:
        """Get all completed results."""
        with self._results_lock:
            return list(self._results)

    def result_count(self) -> int:
        with self._results_lock:
            return len(self._results)

    def wait_all(self, timeout: Optional[float] = None) -> bool:
        """Wait until all submitted tasks are completed."""
        deadline = time.time() + timeout if timeout else float('inf')
        while time.time() < deadline:
            with self._counter_lock:
                if self._total_completed >= self._total_submitted:
                    return True
            time.sleep(0.01)
        return False

    # -- Stats ---------------------------------------------------

    @property
    def stats(self) -> Dict[str, Any]:
        """Get pool statistics."""
        with self._counter_lock:
            submitted = self._total_submitted
            completed = self._total_completed
        with self._steal_lock:
            steals_ok = self._steals_succeeded
            steals_fail = self._steals_failed
        queue_sizes = [q.size for q in self._queues] if self._running else []

        return {
            "workers": self.num_workers,
            "submitted": submitted,
            "completed": completed,
            "pending": submitted - completed,
            "steals_succeeded": steals_ok,
            "steals_failed": steals_fail,
            "steal_success_rate": (steals_ok / max(steals_ok + steals_fail, 1)) * 100,
            "queue_sizes": queue_sizes,
            "load_imbalance": max(queue_sizes) - min(queue_sizes) if queue_sizes else 0,
            "running": self._running,
        }

    # -- Worker Loop ---------------------------------------------

    def _worker_loop(self, worker_id: int):
        """Main worker loop with work-stealing.

        1. Try to pop from own queue (LIFO — cache-local)
        2. If empty, attempt to steal from other workers (FIFO)
        3. If all queues empty, sleep briefly and retry
        4. Check shutdown event between iterations
        """
        own_queue = self._queues[worker_id]

        while self._running and not self._shutdown_event.is_set():
            task = own_queue.pop()

            if task is not None:
                self._execute_task(task)
                continue

            # Own queue empty — try stealing from other workers
            stolen = False
            for attempt in range(self.steal_attempts):
                if not self._running:
                    break
                # Pick victim round-robin (starting from worker_id+1)
                victim_id = (worker_id + 1 + attempt) % self.num_workers
                if victim_id == worker_id:
                    continue
                task = self._queues[victim_id].steal()
                if task is not None:
                    with self._steal_lock:
                        self._steals_succeeded += 1
                    self._execute_task(task)
                    stolen = True
                    break
                else:
                    with self._steal_lock:
                        self._steals_failed += 1

            if not stolen:
                # All queues empty — brief sleep then retry
                if self._shutdown_event.wait(timeout=0.001):
                    break

    def _execute_task(self, task: Dict[str, Any]):
        """Execute a single task and store result."""
        task_id = task["task_id"]
        fn = task["fn"]
        args = task.get("args", ())
        kwargs = task.get("kwargs", {})
        start = time.perf_counter()

        try:
            result = fn(*args, **kwargs)
            success = True
            error = None
        except Exception as e:
            result = None
            success = False
            error = str(e)

        elapsed = (time.perf_counter() - start) * 1000

        with self._results_lock:
            self._results.append({
                "task_id": task_id,
                "success": success,
                "result": result,
                "error": error,
                "elapsed_ms": elapsed,
            })

        with self._counter_lock:
            self._total_completed += 1

    def __repr__(self) -> str:
        s = self.stats
        return (f"WorkStealingPool(workers={s['workers']}, "
                f"done={s['completed']}/{s['submitted']}, "
                f"steals={s['steals_succeeded']}/{s['steals_succeeded'] + s['steals_failed']})")


# -- Device-Aware Parallelism --------------------------------------------------

@dataclass
class DeviceParallelConfig:
    """Optimal parallelism config per hardware device.

    Benchmarks should be re-measured on the target hardware.
    Default values are conservative estimates for a 16-core CPU.
    """

    device_name: str = "CPU"
    max_workers: int = 16
    optimal_workers: int = 16
    min_batch_size: int = 1
    per_worker_memory_mb: int = 64
    free_threaded_speedup: float = 1.0
    gil_limited: bool = True

    @classmethod
    def for_cpu(cls) -> 'DeviceParallelConfig':
        cpu = os.cpu_count() or 16
        return cls(
            device_name="CPU",
            max_workers=cpu,
            optimal_workers=cpu,
            min_batch_size=1,
            per_worker_memory_mb=128,
            free_threaded_speedup=2.0,
            gil_limited=not is_free_threaded(),
        )

    @classmethod
    def for_gpu(cls) -> 'DeviceParallelConfig':
        return cls(
            device_name="GPU",
            max_workers=32,
            optimal_workers=16,
            min_batch_size=4,
            per_worker_memory_mb=256,
            free_threaded_speedup=2.0,
            gil_limited=False,
        )

    @classmethod
    def for_npu(cls) -> 'DeviceParallelConfig':
        return cls(
            device_name="NPU",
            max_workers=32,
            optimal_workers=16,
            min_batch_size=8,
            per_worker_memory_mb=128,
            free_threaded_speedup=4.0,
            gil_limited=False,
        )

    @classmethod
    def for_device(cls, device: str) -> 'DeviceParallelConfig':
        """Auto-detect optimal config for a device name."""
        device = device.upper()
        if device == "CPU":
            return cls.for_cpu()
        elif device in ("GPU", "INTEL_GPU", "ARC", "VULKAN"):
            return cls.for_gpu()
        elif device in ("NPU", "AI_BOOST", "INTEL_NPU"):
            return cls.for_npu()
        return cls.for_cpu()

    def summary(self) -> str:
        return (f"{self.device_name}: {self.optimal_workers}/{self.max_workers} workers, "
                f"{self.free_threaded_speedup:.1f}x speedup, "
                f"GIL-limited={self.gil_limited}")

def detect_devices() -> list[DeviceParallelConfig]:
    """Detect available compute devices and return their parallel configs."""
    configs = [DeviceParallelConfig.for_cpu()]
    try:
        import openvino as ov
        core = ov.Core()
        devices = core.available_devices
        for d in devices:
            if 'GPU' in d.upper():
                configs.append(DeviceParallelConfig.for_gpu())
            elif 'NPU' in d.upper():
                configs.append(DeviceParallelConfig.for_npu())
    except ImportError:
        pass
    return configs


# -- OpenVINO Async Inference Runner -------------------------------------------

class OpenVINOAsyncRunner:
    """Thread-safe async inference runner using OpenVINO AsyncInferQueue.

    Each thread gets a dedicated infer_request via the async queue,
    avoiding thread-safety issues with shared compiled_model state.

    Usage:
        runner = OpenVINOAsyncRunner("model.xml", device="CPU", num_requests=8)
        results = runner.infer_parallel([input1, input2, ...])
    """

    def __init__(self, model_path: str, device: str = "CPU", num_requests: int = 0):
        self.model_path = model_path
        self.device = device
        self._core: Any = None
        self._compiled_model: Any = None
        self._infer_queue: Any = None
        self._num_requests = num_requests or (os.cpu_count() or 8)
        self._lock = threading.Lock()
        self._initialized = False

    def _ensure_initialized(self):
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            import openvino as ov
            self._core = ov.Core()
            if os.path.exists(self.model_path):
                model = self._core.read_model(self.model_path)
            else:
                raise FileNotFoundError(f"Model not found: {self.model_path}")
            self._compiled_model = self._core.compile_model(model, self.device)
            try:
                self._infer_queue = ov.AsyncInferQueue(
                    self._compiled_model, self._num_requests
                )
            except TypeError:
                # Fallback: older OpenVINO versions
                self._infer_queue = ov.AsyncInferQueue(
                    self._compiled_model, self._num_requests
                )
            self._initialized = True

    def infer_sync(self, input_data: dict) -> dict:
        """Single-thread synchronous inference (thread-safe)."""
        self._ensure_initialized()
        return self._compiled_model(input_data)

    def infer_parallel(self, inputs: list[dict], timeout_ms: int = 5000) -> list[dict]:
        """Run inference on multiple inputs using AsyncInferQueue."""
        self._ensure_initialized()
        results: list[Any] = [None] * len(inputs)

        for i, inp in enumerate(inputs):
            self._infer_queue.start_async(inp)

        def callback(request, userdata):
            idx = userdata
            results[idx] = {k: request.get_tensor(k).data for k in request.outputs}

        self._infer_queue.set_callback(callback)
        self._infer_queue.wait_all()
        return results

    def infer_threaded(self, inputs: list[dict], num_threads: int = 0) -> list[dict]:
        """Multi-threaded inference — each thread gets its own infer request."""
        num_threads = num_threads or min(len(inputs), os.cpu_count() or 8)
        chunk_size = max(1, len(inputs) // num_threads)
        results: list[Any] = [None] * len(inputs)

        def worker(thread_idx: int):
            self._ensure_initialized()
            start = thread_idx * chunk_size
            end = start + chunk_size if thread_idx < num_threads - 1 else len(inputs)
            for i in range(start, end):
                results[i] = self._compiled_model(inputs[i])

        threads = []
        for t in range(num_threads):
            thread = threading.Thread(target=worker, args=(t,), name=f"ov-infer-{t}")
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        return results

    @property
    def available_devices(self) -> list[str]:
        try:
            import openvino as ov
            return ov.Core().available_devices
        except ImportError:
            return []

    def __repr__(self) -> str:
        return (f"OpenVINOAsyncRunner(model={Path(self.model_path).name}, "
                f"device={self.device}, requests={self._num_requests}, "
                f"init={self._initialized})")


# -- Performance Benchmarking --------------------------------------------------

@dataclass
class BenchmarkResult:
    """Result of a parallel benchmark run."""

    num_workers: int
    total_iterations: int
    duration_sec: float
    throughput_per_sec: float
    speedup_vs_baseline: float
    free_threaded: bool
    device: str = "CPU"

    def __repr__(self) -> str:
        return (f"Benchmark(workers={self.num_workers}, "
                f"throughput={self.throughput_per_sec:.1f}/s, "
                f"speedup={self.speedup_vs_baseline:.2f}x)")

def benchmark_parallel(
    workload_fn: Callable[[], Any],
    max_workers: int = 0,
    total_iterations: int = 1000,
    device: str = "CPU",
) -> list[BenchmarkResult]:
    """Benchmark parallel throughput at increasing concurrency levels.

    Args:
        workload_fn: Callable that performs one unit of work (called per iteration).
        max_workers: Max concurrency to test. Default: cpu_count * 2.
        total_iterations: Total iterations to distribute across workers.
        device: Device label for reporting.

    Returns:
        List of BenchmarkResult at each concurrency level [1, 2, 4, 8, 16, 24, 32].
    """
    max_workers = max_workers or (os.cpu_count() or 16) * 2
    levels = [1, 2, 4, 8, 16, 24, 32]
    levels = [l for l in levels if l <= max_workers]

    results = []
    baseline = None
    for workers in levels:
        iters_per_worker = max(1, total_iterations // workers)

        def worker_task():
            for _ in range(iters_per_worker):
                workload_fn()

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(worker_task) for _ in range(workers)]
            for f in as_completed(futures):
                f.result()
        duration = time.perf_counter() - start

        throughput = total_iterations / duration if duration > 0 else 0
        if baseline is None:
            baseline = throughput
        speedup = throughput / baseline if baseline and baseline > 0 else 1.0

        results.append(BenchmarkResult(
            num_workers=workers,
            total_iterations=total_iterations,
            duration_sec=duration,
            throughput_per_sec=throughput,
            speedup_vs_baseline=speedup,
            free_threaded=is_free_threaded(),
            device=device,
        ))

    return results

def print_benchmark_table(results: list[BenchmarkResult]) -> None:
    """Print a formatted table of benchmark results."""
    ft = results[0].free_threaded if results else False
    gil_label = "NO-GIL (free-threaded)" if ft else "GIL ENABLED"
    line = '-' * 50
    print(f"\n{'='*70}")
    print(f"  Parallel Benchmark -- {gil_label}")
    print(f"  Device: {results[0].device if results else 'CPU'}")
    print(f"  {'Workers':<10} {'Throughput':<15} {'Speedup':<10} {'Duration':<10}")
    print(f"  {line}")
    for r in results:
        print(f"  {r.num_workers:<10} {r.throughput_per_sec:>10.1f}/s  {r.speedup_vs_baseline:>6.2f}x   {r.duration_sec:>7.2f}s")
    if len(results) > 1:
        best = max(results, key=lambda r: r.throughput_per_sec)
        print(f"  {line}")
        print(f"  Best: {best.num_workers} workers @ {best.throughput_per_sec:.1f}/s "
              f"({best.speedup_vs_baseline:.2f}x speedup)")


# -- Thread Affinity (Windows) -------------------------------------------------

def set_thread_affinity(thread: threading.Thread, cpu_mask: int | list[int]) -> bool:
    """Pin a thread to specific CPU core(s) on Windows.

    Args:
        thread: The threading.Thread to pin.
        cpu_mask: Integer bitmask or list of core indices (0-based).

    Returns:
        True if affinity was set successfully.
    """
    if sys.platform != 'win32':
        return False

    kernel32 = ctypes.windll.kernel32
    THREAD_SET_INFORMATION = 0x20

    if isinstance(cpu_mask, list):
        mask = sum(1 << core for core in cpu_mask)
    else:
        mask = cpu_mask

    thread_handle = kernel32.OpenThread(THREAD_SET_INFORMATION, False, thread.ident)
    if not thread_handle:
        return False

    try:
        result = kernel32.SetThreadAffinityMask(thread_handle, mask)
        return result != 0
    finally:
        kernel32.CloseHandle(thread_handle)

def set_current_thread_affinity(cpu_mask: int | list[int]) -> bool:
    """Pin the current thread to specific CPU core(s)."""
    if sys.platform != 'win32':
        return False

    kernel32 = ctypes.windll.kernel32

    if isinstance(cpu_mask, list):
        mask = sum(1 << core for core in cpu_mask)
    else:
        mask = cpu_mask

    result = kernel32.SetThreadAffinityMask(
        kernel32.GetCurrentThread(), mask
    )
    return result != 0


# -- Concurrent Task Runner ----------------------------------------------------

class ConcurrentTaskRunner:
    """Run multiple tasks across devices concurrently using free-threading.

    Manages a pool of per-device inference runners that can execute
    truly in parallel under Python 3.14t.

    Usage:
        runner = ConcurrentTaskRunner()
        runner.set_task("cpu", cpu_work_fn)
        runner.set_task("gpu", gpu_work_fn)
        runner.set_task("npu", npu_work_fn)
        results = runner.run_all()
    """

    def __init__(self):
        self._tasks: dict[str, Callable] = {}
        self._configs: dict[str, DeviceParallelConfig] = {}
        self._results: dict[str, Any] = ThreadSafeDict()
        self._errors: ThreadSafeList = ThreadSafeList()

    def set_task(self, device: str, task_fn: Callable[[], Any]) -> None:
        """Register a task function for a device."""
        self._tasks[device] = task_fn
        self._configs[device] = DeviceParallelConfig.for_device(device)

    def run_all(self, timeout_sec: float = 300.0) -> dict[str, Any]:
        """Run all registered tasks in parallel threads and collect results."""
        if not self._tasks:
            return {'results': {}, 'errors': [], 'free_threaded': is_free_threaded(), 'devices_used': 0}

        def run_device(device: str, task_fn: Callable):
            try:
                config = self._configs.get(device, DeviceParallelConfig.for_cpu())
                start = time.perf_counter()
                result = task_fn()
                elapsed = time.perf_counter() - start
                self._results[f"{device}_result"] = result
                self._results[f"{device}_elapsed_sec"] = elapsed
                self._results[f"{device}_workers"] = config.optimal_workers
            except Exception as e:
                self._errors.append({'device': device, 'error': str(e)})

        threads = []
        for device, task_fn in self._tasks.items():
            t = threading.Thread(target=run_device, args=(device, task_fn),
                                name=f"task-{device.lower()}")
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=timeout_sec)

        report: dict[str, Any] = {
            'results': {k: v for k, v in self._results.items()},
            'errors': self._errors.get_all(),
            'free_threaded': is_free_threaded(),
        }

        if report['results']:
            total_elapsed = max(
                (v for k, v in report['results'].items() if k.endswith('_elapsed_sec')),
                default=0
            )
            report['total_duration_sec'] = total_elapsed
            report['devices_used'] = len(self._tasks)

        return report


# -- Diagnostic Entry Point ----------------------------------------------------

def main():
    """Run free-threading diagnostics."""
    sep = '=' * 70
    sub = '-' * 50
    print(f"\n{sep}")
    print(f"  Free-Threading Utility Diagnostics")
    print(f"  {sep}")
    print_thread_info()

    print(f"\n  {sub}")
    print(f"  FreeThreadPool default config:")
    for k, v in FreeThreadPool.info().items():
        print(f"    {k}: {v}")

    print(f"\n  {sub}")
    print(f"  Device Parallel Configs:")
    for device in ["CPU", "GPU", "NPU"]:
        cfg = DeviceParallelConfig.for_device(device)
        print(f"    {cfg.summary()}")

    print(f"\n  {sub}")
    print(f"  Quick benchmark: pure-Python math workload")

    def math_work():
        total = sum(i * i for i in range(5000))
        return total

    results = benchmark_parallel(math_work, max_workers=16, total_iterations=500,
                                 device="CPU")
    print_benchmark_table(results)

    print(f"\n{sep}")


if __name__ == '__main__':
    main()
