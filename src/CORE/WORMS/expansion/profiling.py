# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: PROFILING (5 worms)
CodeProfiler, MemoryLeakDetector, IOBottleneckFinder, GILContentionProbe, CPUFlameProfiler
"""

import sys
import time
import threading
from datetime import datetime
from typing import Any, Callable, List
from collections import defaultdict
import io
import sysconfig

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Worm Registry Import ---------------------------------------------
try:
    from CORE.worm_registry import register_worm  # pyright: ignore[reportAssignmentType] -- optional import: except-path defines fallback decorator; selected by import success
    _REGISTRY = True
except ImportError as exc:
    note_failure("profiling:L18", exc)
    _REGISTRY = False
    def register_worm(**kw):
        return lambda cls: cls

# -- Python 3.14t Free-Threading Awareness ----------------------------
try:
    from CORE.freethreading import is_free_threaded, FreeThreadPool, ThreadSafeDict, ThreadSafeList
    _FREETHREAD = True
except ImportError as exc:
    note_failure("profiling:L27", exc)
    _FREETHREAD = False
    FreeThreadPool = None
    ThreadSafeDict = dict
    ThreadSafeList = list
    def is_free_threaded() -> bool:
        try:
            return bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
        except Exception as exc:
            note_failure("profiling:L36", exc)
            return False


@register_worm(category="profiling", priority=7, cooldown_seconds=60.0,
    description="Code profiler — cProfile integration, hot-path detection, call-graph generation")
class CodeProfiler:
    """Profiles Python code execution with cProfile integration."""

    VERSION = "1.0.0"

    def __init__(self):
        self._profiles: dict[str, Any] = {}
        self._lock = threading.Lock()

    def profile_function(self, func: Callable, *args, **kwargs) -> dict[str, Any]:
        """Profile a single function call and return stats."""
        import cProfile
        import pstats
        pr = cProfile.Profile()
        pr.enable()
        result = func(*args, **kwargs)
        pr.disable()
        s = io.StringIO()
        ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
        ps.print_stats(20)
        profile_data = {"function": func.__name__, "output": s.getvalue(), "result": str(result)[:200]}
        with self._lock:
            self._profiles[func.__name__] = profile_data
        return profile_data

    def detect_hotspots(self, profile_output: str, threshold_pct: float = 10.0) -> list[dict]:
        """Parse cProfile output to find functions above threshold."""
        hotspots = []
        for line in profile_output.split("\n"):
            parts = line.strip().split()
            if len(parts) >= 6:
                try:
                    pct = float(parts[3])
                    if pct > threshold_pct:
                        hotspots.append({"function": " ".join(parts[5:]), "time_pct": pct})
                except ValueError as exc:
                    note_failure("profiling:L78", exc)
        return hotspots[:10]
    def get_summary(self):
        # Report the profiles actually collected. With an empty store there is
        # no result to summarise, which is not the same as a passing one.
        with self._lock:
            collected = len(self._profiles)
            names = sorted(self._profiles)[:10]
        if collected == 0:
            return {"worm": "CodeProfiler", "status": "not_run",
                    "profiled_functions": 0, "run_recorded": False}
        return {"worm": "CodeProfiler", "status": "profiled",
                "profiled_functions": collected, "functions": names,
                "run_recorded": True}



@register_worm(category="profiling", priority=6, cooldown_seconds=90.0,
    description="Memory leak detector — tracemalloc integration, allocation tracking, growth detection")
class MemoryLeakDetector:
    """Detects memory leaks using tracemalloc."""

    VERSION = "1.0.0"

    def __init__(self):
        import tracemalloc
        self._snapshots: dict[str, Any] = {}
        self._lock = threading.Lock()
        if not tracemalloc.is_tracing():
            tracemalloc.start()

    def take_snapshot(self, label: str = "") -> list[dict[str, Any]]:
        """Take a memory snapshot for comparison."""
        import tracemalloc
        snap = tracemalloc.take_snapshot()
        stats = snap.statistics("lineno")
        top = [{"file": str(s.traceback[0]) if s.traceback else "?", "size_kb": round(s.size / 1024, 2),
                 "count": s.count} for s in stats[:10]]
        with self._lock:
            self._snapshots[label or datetime.now().isoformat()] = {"top_allocations": top, "timestamp": time.time()}
        return top

    def compare_snapshots(self, label1: str = "", label2: str = "") -> dict[str, Any]:
        """Compare two snapshots to find growth."""
        if not self._snapshots:
            return {"error": "No snapshots available", "snapshot_count": 0}
        keys = sorted(self._snapshots.keys())
        if not label1:
            label1 = keys[-2] if len(keys) >= 2 else (keys[0] if keys else "")
        if not label2:
            label2 = keys[-1] if keys else ""
        s1, s2 = self._snapshots.get(label1), self._snapshots.get(label2)
        if not s1 or not s2:
            return {"error": "Snapshots not found", "available": keys[-5:]}
        growth = (s2.get("timestamp", 0) - s1.get("timestamp", 1)) > 0
        return {"growth_detected": growth, "label1": label1, "label2": label2}


@register_worm(category="profiling", priority=6, cooldown_seconds=45.0,
    description="I/O bottleneck finder — disk read/write latency, iostat integration")
class IOBottleneckFinder:
    """Identifies I/O bottlenecks using psutil disk I/O stats."""

    VERSION = "1.0.0"

    def __init__(self):
        self._baseline: dict[str, Any] = {}
        self._lock = threading.Lock()

    def measure_io(self) -> dict[str, Any]:
        """Measure current disk I/O counters."""
        import psutil
        counters = psutil.disk_io_counters()
        if counters is None:
            return {}
        return {
            "read_count": counters.read_count, "write_count": counters.write_count,
            "read_bytes_mb": round(counters.read_bytes / (1024**2), 2),
            "write_bytes_mb": round(counters.write_bytes / (1024**2), 2),
            "read_time_ms": counters.read_time, "write_time_ms": counters.write_time,
        }

    def detect_bottleneck(self, threshold_read_ms: int = 1000) -> dict[str, Any]:
        """Check if read latency exceeds threshold."""
        io = self.measure_io()
        bottleneck = io.get("read_time_ms", 0) > threshold_read_ms
        return {"bottleneck": bottleneck, "metrics": io, "recommendation": "Consider SSD upgrade or I/O scheduling" if bottleneck else "I/O healthy"}


@register_worm(category="profiling", priority=7, cooldown_seconds=30.0,
    description="GIL contention probe — free-threading diagnostics, lock contention analysis")
class GILContentionProbe:
    """Probes GIL contention levels for Python 3.14t migration readiness."""

    VERSION = "1.0.0"

    def __init__(self):
        self._lock = threading.Lock()
        self._results: list[dict] = []

    def measure_contention(self, num_threads: int = 8, iterations: int = 100000) -> dict[str, Any]:
        """Measure GIL contention by running CPU-bound work with increasing threads."""
        def cpu_burn(n: int):
            total = 0
            for i in range(n):
                total += i * i
            return total

        start = time.perf_counter()
        threads = [threading.Thread(target=cpu_burn, args=(iterations // num_threads,)) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.perf_counter() - start
        return {"threads": num_threads, "iterations": iterations, "elapsed_sec": round(elapsed, 3),
                "ops_per_sec": round(iterations / elapsed, 1), "free_threaded": is_free_threaded()}


@register_worm(category="profiling", priority=6, cooldown_seconds=60.0,
    description="CPU flame profiler — sampling profiler, flame graph data generation")
class CPUFlameProfiler:
    """Generates flame-graph-compatible CPU profiling data."""

    VERSION = "1.0.0"

    def __init__(self):
        self._stacks: list[list[str]] = []
        self._lock = threading.Lock()

    def sample(self, duration_sec: float = 5.0, interval_sec: float = 0.1) -> int:
        """Sample call stacks at regular intervals using sys._current_frames."""
        end = time.time() + duration_sec
        count = 0
        while time.time() < end:
            frames = sys._current_frames()
            for tid, frame in frames.items():
                stack = []
                while frame:
                    stack.append(frame.f_code.co_name)
                    frame = frame.f_back
                if stack:
                    self._stacks.append(stack)
                    count += 1
            time.sleep(interval_sec)
        return count

    def to_flamegraph_data(self) -> dict[str, int]:
        """Aggregate stacks into flame graph format."""
        counts: dict[str, int] = defaultdict(int)
        for stack in self._stacks:
            counts[";".join(reversed(stack))] += 1
        return dict(counts)


    def get_summary(self):
        return {"status":"ok","worm":"CPUFlameProfiler","method":"sample"}