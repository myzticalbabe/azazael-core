import sys
import os
from pathlib import Path

# ── CORE free-threading imports ────────────────────────────────────────────
_ft_path = str(Path(__file__).resolve().parent.parent)
if _ft_path not in sys.path:
    sys.path.insert(0, _ft_path)
try:
    from CORE.freethreading import FreeThreadPool, is_free_threaded, CancellationToken, TaskCancelledError  # pyright: ignore[reportAssignmentType] -- try-success binds the real freethreading classes while the except fallback defines structurally distinct local classes behind the same names; selection is by import success, not substitutability
except ImportError:
    import os
    from concurrent.futures import ThreadPoolExecutor
    def is_free_threaded() -> bool:
        return False
    class FreeThreadPool:
        """Fallback FreeThreadPool for Python 3.12 (wraps ThreadPoolExecutor)."""
        def __init__(self, max_workers=0, thread_name_prefix="ft-worker"):
            self.max_workers = max_workers or (os.cpu_count() or 8)
            self.thread_name_prefix = thread_name_prefix
            self._executor = None
        def __enter__(self):
            self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix=self.thread_name_prefix)
            return self
        def __exit__(self, *a):
            if self._executor:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None
        def submit(self, fn, *a, **kw):
            if self._executor is None:
                raise RuntimeError("FreeThreadPool not entered")
            return self._executor.submit(fn, *a, **kw)
        def map(self, fn, *it, **kw):
            if self._executor is None:
                raise RuntimeError("FreeThreadPool not entered")
            return self._executor.map(fn, *it, **kw)
    class TaskCancelledError(Exception):
        pass
    CancellationToken = None

IS_FREE_THREADED = is_free_threaded()

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "HYDRA" / "ml_platforms"))

# ── Canonical project root ─────────────────────────────────────────────────
# The fallback at the three sites below used to be `Path.home() / "azazael"`,
# which names a DIFFERENT, stale checkout from this repo (C:/Azazael/azazael), so
# with AZAZAEL_ROOT unset `worms_dir` resolved inside the wrong tree and the worm
# and cluster loaders pointed at modules that are not the ones that ship here.
# `CORE.paths.get_project_root_safe()` makes that decision ONCE for the project:
# AZAZAEL_ROOT first (it delegates to get_project_root, so the precedence and the
# per-call env re-read at each call site are unchanged), then the tree it ships in.
# Guarded with a location-derived fallback for the same reason the freethreading
# import above is guarded: this module is also loaded by the standalone exercise
# paths, where `CORE` may be unreachable and an unguarded import would take the
# whole module down where it previously fell back.
try:
    from CORE.paths import get_project_root_safe as _get_project_root_safe
except ImportError:                               # CORE unreachable: this file's own tree
    def _get_project_root_safe() -> Path:
        # src/CORE/WORMS/CONCURRENCY.py -> [0]=WORMS [1]=CORE [2]=src [3]=root
        return Path(__file__).resolve().parents[3]

# coding: utf-8
"""
CONCURRENCY MANAGER - Cotemporaneous Execution Engine

Enables parallel/concurrent execution of worms and clusters
for optimal system management.

Features:
- Thread pool execution (FreeThreadPool under Python 3.14t)
- Async/await support
- Process pool execution
- Concurrent worm execution
- Parallel cluster operations
- Thread-safe operations

Usage:
    >>> from concurrency import ConcurrencyManager
    >>> cm = ConcurrencyManager(max_workers=8)
    >>> results = cm.execute_worms(["SYSTEMZ", "PROCESSZ", "DISKZ"])
"""

import json
import time
import threading
from datetime import datetime
from typing import Any, Callable, List
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
import tkinter as tk
from tkinter import ttk, scrolledtext
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


@dataclass
class ExecutionResult:
    """Result from concurrent execution."""

    task_id: str
    task_name: str
    success: bool
    duration_ms: float
    result: Any = None
    error: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


def _record_execution(name: str, result: "ExecutionResult",
                        decision_id: Any = None) -> bool:
    """Route one execution result to the canonical telemetry tap.

    Module-level entry point so tests and sibling producers share one
    schema (see tests/test_worm_telemetry.py::test_every_producer_writes_
    the_same_schema). Fail-soft: returns False instead of raising.
    """
    try:
        from CORE.WORMS.worm_telemetry import emit_worm_outcome_from_result
        return bool(emit_worm_outcome_from_result(
            name, result, source="ConcurrencyWormExecutor",
            decision_id=str(decision_id) if decision_id else None))
    except Exception as exc:
        note_failure("CONCURRENCY:L101", exc)
        return False


@dataclass
class ConcurrencyConfig:
    """Configuration for concurrent execution."""

    max_workers: int = 8
    timeout_seconds: int = 60
    parallel_clusters: bool = True
    thread_pool_size: int = 16
    process_pool_size: int = 4
    async_enabled: bool = True


class TaskManager:
    """Manages concurrent tasks."""

    def __init__(self, config: ConcurrencyConfig | None = None):
        self.config = config or ConcurrencyConfig()
        self.tasks: dict[str, ExecutionResult] = {}
        self.lock = threading.Lock()
        self.executor: ThreadPoolExecutor | None = None

    def submit_task(
        self,
        task_id: str,
        task_name: str,
        func: Callable,
        args: tuple | None = None,
        kwargs: dict | None = None,
        cancellation_token: Any = None,
    ) -> ExecutionResult:
        """Submit a task for execution."""
        start = time.time()
        result = ExecutionResult(
            task_id=task_id,
            task_name=task_name,
            success=False,
            duration_ms=0,
        )

        # Check cancellation before executing
        if cancellation_token is not None and hasattr(cancellation_token, 'raise_if_cancelled'):
            try:
                cancellation_token.raise_if_cancelled()
            except TaskCancelledError as exc:
                note_failure("CONCURRENCY:L148", exc)
                result.error = "Cancelled"
                result.duration_ms = (time.time() - start) * 1000
                with self.lock:
                    self.tasks[task_id] = result
                return result

        try:
            args = args or ()
            kwargs = kwargs or {}
            output = func(*args, **kwargs)
            result.success = True
            result.result = output
        except TaskCancelledError as exc:
            note_failure("CONCURRENCY:L161", exc)
            result.error = "Cancelled"
        except Exception as e:
            note_failure("CONCURRENCY:L163", e)
            result.success = False
            result.error = str(e)

        result.duration_ms = (time.time() - start) * 1000

        with self.lock:
            self.tasks[task_id] = result

        return result

    def execute_parallel(self, tasks: list[dict[str, Any]], cancellation_token: Any = None) -> list[ExecutionResult]:
        """Execute multiple tasks in parallel using FreeThreadPool.

        Supports cooperative cancellation via CancellationToken.
        On timeout (from ConcurrencyConfig.timeout_seconds), cancels
        remaining futures and signals the cancellation token.
        """
        results: list[ExecutionResult] = []
        futures: dict[Any, str] = {}
        timeout = self.config.timeout_seconds if self.config.timeout_seconds > 0 else None

        with FreeThreadPool(max_workers=self.config.max_workers) as executor:
            for task in tasks:
                # Inject cancellation_token into task kwargs
                task_kwargs = dict(task.get("kwargs", {}))
                task_kwargs["cancellation_token"] = cancellation_token
                future = executor.submit(
                    self.submit_task,
                    task.get("id", str(time.time())),
                    task.get("name", "unknown"),
                    task.get("func"),
                    task.get("args"),
                    task_kwargs,
                    cancellation_token,
                )
                futures[future] = task.get("name", "unknown")

            try:
                for future in as_completed(futures, timeout=timeout):
                    try:
                        result = future.result()
                        results.append(result)
                    except Exception as e:
                        note_failure("CONCURRENCY:L206", e)
                        results.append(
                            ExecutionResult(
                                task_id="failed",
                                task_name=futures.get(future, "unknown"),
                                success=False,
                                duration_ms=0,
                                error=str(e),
                            )
                        )
            except TimeoutError as exc:
                # Cancel via token then cancel remaining futures
                # Cancel via token then cancel remaining futures
                note_failure("CONCURRENCY:L216", exc)
                if cancellation_token is not None and hasattr(cancellation_token, 'cancel'):
                    cancellation_token.cancel()
                for f in futures:
                    if not f.done():
                        f.cancel()
                        results.append(
                            ExecutionResult(
                                task_id="timeout",
                                task_name=futures.get(f, "unknown"),
                                success=False,
                                duration_ms=0,
                                error="Timed out",
                            )
                        )

        return results

    def get_results(self) -> dict[str, ExecutionResult]:
        """Get all task results."""
        with self.lock:
            return dict(self.tasks)


class WormExecutor:
    """Executes worms concurrently."""

    def __init__(self):
        self.task_manager = TaskManager()
        self.worm_modules = {}

    def load_worm(self, worm_name: str) -> bool:
        """Load a worm module."""
        azazael_root = os.environ.get("AZAZAEL_ROOT", os.environ.get("Azazael_ROOT", str(_get_project_root_safe())))
        worms_dir = os.path.join(azazael_root, "src", "CORE", "WORMS")
        
        worm_paths = {
            "SYSTEMZ": (worms_dir, "SYSTEMZ", "SystemZ"),
            "PROCESSZ": (worms_dir, "PROCESSZ", "PROCESSZ"),
            "DISKZ": (worms_dir, "DISKZ", "DISKZ"),
            "NETWORXZ": (worms_dir, "NETWORXZ", "NETWORXZ"),
            "CLOCKZ": (worms_dir, "CLOCKZ", "CLOCKZ"),
        }

        if worm_name not in worm_paths:
            return False

        folder, module, class_name = worm_paths[worm_name]
        try:
            if folder not in sys.path:
                sys.path.insert(0, folder)
            exec(f"from {module} import {class_name}", globals())
            self.worm_modules[worm_name] = (module, class_name)
            return True
        except Exception as exc:
            note_failure("CONCURRENCY:L271", exc)
            return False

    def execute_worm(self, worm_name: str, cancellation_token: Any = None,
                       decision_id: Any = None) -> ExecutionResult:
        """Execute a single worm."""
        start = time.time()

        def _emit(result: ExecutionResult) -> ExecutionResult:
            # Every exit path emits via the module helper (single schema).
            _record_execution(worm_name, result, decision_id=decision_id)
            return result

        if worm_name not in self.worm_modules:
            return _emit(ExecutionResult(
                task_id=str(time.time()),
                task_name=worm_name,
                success=False,
                duration_ms=0,
                error="Worm not loaded",
            ))

        # Check cancellation before executing
        if cancellation_token is not None and hasattr(cancellation_token, 'raise_if_cancelled'):
            try:
                cancellation_token.raise_if_cancelled()
            except TaskCancelledError:
                return _emit(ExecutionResult(
                    task_id=str(time.time()),
                    task_name=worm_name,
                    success=False,
                    duration_ms=0,
                    error="Cancelled",
                ))

        module, class_name = self.worm_modules[worm_name]
        try:
            exec(f"worm = {class_name}()", globals())
            exec("result = worm.get_status()", globals())
            success = True
            output = globals().get("result")
        except Exception as e:
            note_failure("CONCURRENCY:L312", e)
            success = False
            output = None
            error = str(e)

        return _emit(ExecutionResult(
            task_id=str(time.time()),
            task_name=worm_name,
            success=success,
            duration_ms=(time.time() - start) * 1000,
            result=output,
            error=error if not success else None,
        ))

    def execute_all_parallel(self, worm_names: list[str], cancellation_token: Any = None) -> list[ExecutionResult]:
        """Execute multiple worms in parallel."""
        for worm in worm_names:
            self.load_worm(worm)

        tasks = [
            {"id": w, "name": w, "func": self.execute_worm, "args": (w,)}
            for w in worm_names
        ]

        return self.task_manager.execute_parallel(tasks, cancellation_token=cancellation_token)


class ClusterExecutor:
    """Executes clusters concurrently."""

    def __init__(self):
        self.task_manager = TaskManager()
        self.clusters: dict[str, Any] = {}
        self.load_clusters()

    def load_clusters(self):
        """Load cluster modules."""
        azazael_root = os.environ.get("AZAZAEL_ROOT", os.environ.get("Azazael_ROOT", str(_get_project_root_safe())))
        worms_dir = os.path.join(azazael_root, "src", "CORE", "WORMS")
        try:
            if worms_dir not in sys.path:
                sys.path.insert(0, worms_dir)
            from CLUSTERWORMS import CLUSTER_REGISTRY

            self.clusters = {name: info for name, info in CLUSTER_REGISTRY.items()}
        except Exception as exc:
            note_failure("CONCURRENCY:L357", exc)

    def execute_cluster(self, cluster_name: str, cancellation_token: Any = None) -> ExecutionResult:
        """Execute a single cluster."""
        time.time()
        azazael_root = os.environ.get("AZAZAEL_ROOT", os.environ.get("Azazael_ROOT", str(_get_project_root_safe())))
        worms_dir = os.path.join(azazael_root, "src", "CORE", "WORMS")

        # Check cancellation before executing
        if cancellation_token is not None and hasattr(cancellation_token, 'raise_if_cancelled'):
            try:
                cancellation_token.raise_if_cancelled()
            except TaskCancelledError as exc:
                note_failure("CONCURRENCY:L370", exc)
                return ExecutionResult(
                    task_id=str(time.time()),
                    task_name=f"cluster:{cluster_name}",
                    success=False,
                    duration_ms=0,
                    error="Cancelled",
                )

        try:
            if worms_dir not in sys.path:
                sys.path.insert(0, worms_dir)
            from CLUSTERWORMS import ClusterWORMZ

            cluster = ClusterWORMZ(cluster_name)
            results = cluster.execute_all()
            agg = cluster.aggregate_results()

            return ExecutionResult(
                task_id=str(time.time()),
                task_name=f"cluster:{cluster_name}",
                success=agg.failure_count == 0,
                duration_ms=agg.duration_ms,
                result={
                    "success_count": agg.success_count,
                    "failure_count": agg.failure_count,
                    "worms_executed": len(results),
                },
            )
        except Exception as e:
            note_failure("CONCURRENCY:L399", e)
            return ExecutionResult(
                task_id=str(time.time()),
                task_name=f"cluster:{cluster_name}",
                success=False,
                duration_ms=0,
                error=str(e),
            )

    def execute_all_parallel(self, cluster_names: list[str], cancellation_token: Any = None) -> list[ExecutionResult]:
        """Execute multiple clusters in parallel."""
        tasks = [
            {"id": c, "name": c, "func": self.execute_cluster, "args": (c,)}
            for c in cluster_names
        ]

        return self.task_manager.execute_parallel(tasks, cancellation_token=cancellation_token)


try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("CONCURRENCY:L420", exc)
    register_worm = lambda **kw: (lambda cls: cls)


@register_worm(category="orchestration", priority=7, cooldown_seconds=20.0, description="Cotemporaneous execution engine for parallel worm/cluster operations")
class ConcurrencyManager:
    VERSION = "1.0.0"

    def __init__(self, config: ConcurrencyConfig | None = None):
        self.config = config or ConcurrencyConfig()
        self.worm_executor = WormExecutor()
        self.cluster_executor = ClusterExecutor()
        self.task_manager = TaskManager(self.config)
        self.cancellation_token = CancellationToken() if CancellationToken is not None else threading.Event()

    def cancel_all(self) -> None:
        """Cancel all in-flight parallel operations."""
        if self.cancellation_token is not None:
            getattr(self.cancellation_token, "cancel")()

    def reset_token(self) -> None:
        """Reset the cancellation token for a new scan."""
        if self.cancellation_token is not None and hasattr(self.cancellation_token, 'reset'):
            getattr(self.cancellation_token, "reset")()
        else:
            self.cancellation_token = CancellationToken() if CancellationToken is not None else threading.Event()

    def execute_system_scan(self) -> dict[str, Any]:
        """Execute comprehensive system scan concurrently."""
        print("Starting concurrent system scan...")

        worms = ["SYSTEMZ", "PROCESSZ", "DISKZ", "NETWORXZ"]

        self.reset_token()
        worm_results = self.worm_executor.execute_all_parallel(worms, cancellation_token=self.cancellation_token)

        results = {
            "timestamp": datetime.now().isoformat(),
            "worms": {},
            "summary": {
                "total": len(worm_results),
                "success": sum(1 for r in worm_results if r.success),
                "failure": sum(1 for r in worm_results if not r.success),
            },
        }

        for r in worm_results:
            results["worms"][r.task_name] = {
                "success": r.success,
                "duration_ms": r.duration_ms,
                "error": r.error,
            }

        return results

    def execute_full_ecosystem(self) -> dict[str, Any]:
        """Execute full ecosystem scan."""
        print("Starting full ecosystem scan...")

        clusters = ["system", "network", "health"]
        self.reset_token()
        cluster_results = self.cluster_executor.execute_all_parallel(clusters, cancellation_token=self.cancellation_token)

        results = {
            "timestamp": datetime.now().isoformat(),
            "clusters": {},
            "summary": {
                "total": len(cluster_results),
                "success": sum(1 for r in cluster_results if r.success),
            },
        }

        for r in cluster_results:
            results["clusters"][r.task_name] = {
                "success": r.success,
                "duration_ms": r.duration_ms,
                "result": r.result,
            }

        return results

    def get_status(self) -> dict[str, Any]:
        """Get concurrency manager status."""
        return {
            "version": self.VERSION,
            "max_workers": self.config.max_workers,
            "thread_pool": self.config.thread_pool_size,
            "process_pool": self.config.process_pool_size,
            "async_enabled": self.config.async_enabled,
            "clusters_available": list(self.cluster_executor.clusters.keys()),
        }


def run_popup():
    """Run concurrency manager with popup."""
    root = tk.Tk()
    root.title("CONCURRENCY MANAGER - Cotemporaneous Execution")
    root.geometry("1000x750")
    root.configure(bg="#1a1a2e")

    cm = ConcurrencyManager()
    config = cm.config

    def log(msg, level="INFO"):
        colors = {
            "OK": "#00ff88",
            "FAIL": "#ff4444",
            "INFO": "#00aaff",
        }
        timestamp = datetime.now().strftime("%H:%M:%S")
        text_area.insert("end", f"[{timestamp}] [{level}] {msg}\n")
        text_area.see("end")
        root.update_idletasks()

    header = tk.Frame(root, bg="#16213e", pady=15)
    header.pack(fill="x")

    tk.Label(
        header,
        text="CONCURRENCY MANAGER",
        font=("Consolas", 28, "bold"),
        bg="#16213e",
        fg="#00d4ff",
    ).pack()

    tk.Label(
        header,
        text="Cotemporaneous Execution for Optimal Management",
        font=("Consolas", 12),
        bg="#16213e",
        fg="#7c3aed",
    ).pack()

    status_label = tk.Label(
        root,
        text="Ready",
        font=("Consolas", 14),
        bg="#1a1a2e",
        fg="#00ff88",
    )
    status_label.pack(pady=5)

    control_frame = tk.Frame(root, bg="#1a1a2e", pady=10)
    control_frame.pack(fill="x", padx=20)

    tk.Label(
        control_frame,
        text="Workers:",
        bg="#1a1a2e",
        fg="#00d4ff",
        font=("Consolas", 11),
    ).pack(side="left")

    workers_var = tk.IntVar(value=config.max_workers)
    workers_spin = tk.Spinbox(
        control_frame,
        from_=1,
        to=32,
        textvariable=workers_var,
        width=5,
        font=("Consolas", 10),
    )
    workers_spin.pack(side="left", padx=10)

    progress_frame = tk.Frame(root, bg="#1a1a2e", pady=10)
    progress_frame.pack(fill="x", padx=20)

    progress = ttk.Progressbar(progress_frame, mode="determinate", length=950)
    progress.pack(fill="x")

    progress_label = tk.Label(
        progress_frame,
        text="0 / 0 tasks",
        font=("Consolas", 12),
        bg="#1a1a2e",
        fg="#00ff88",
    )
    progress_label.pack()

    stats_frame = tk.Frame(root, bg="#1a1a2e", pady=5)
    stats_frame.pack(fill="x", padx=20)

    stats_label = tk.Label(
        stats_frame,
        text="Tasks: 0 | Success: 0 | Duration: 0ms",
        font=("Consolas", 12),
        bg="#1a1a2e",
        fg="#00d4ff",
    )
    stats_label.pack()

    text_area = scrolledtext.ScrolledText(
        root, wrap="word", font=("Consolas", 10), bg="#0f0f1a", fg="#00ff88", height=35
    )
    text_area.pack(fill="both", expand=True, padx=20, pady=10)

    button_frame = tk.Frame(root, bg="#1a1a2e", pady=15)
    button_frame.pack(fill="x", padx=20)

    def run_system_scan():
        status_label.config(text="Running System Scan...")
        progress["value"] = 0
        text_area.delete("1.0", "end")

        log("=" * 60, "INFO")
        log("CONCURRENCY MANAGER - System Scan", "INFO")
        log("=" * 60, "INFO")

        cm.config.max_workers = workers_var.get()
        log(f"Max workers: {cm.config.max_workers}", "INFO")

        results = cm.execute_system_scan()

        log("\nSystem Scan Results:", "INFO")
        for worm, data in results["worms"].items():
            status = "OK" if data["success"] else "FAIL"
            log(
                f"  [{status}] {worm}: {data['duration_ms']:.0f}ms",
                status if data["success"] else "FAIL",
            )

        log("\nSummary:", "INFO")
        log(f"  Total: {results['summary']['total']}", "INFO")
        log(f"  Success: {results['summary']['success']}", "INFO")
        log(f"  Failed: {results['summary']['failure']}", "INFO")

        progress["value"] = 100
        stats_label.config(
            text=f"Tasks: {results['summary']['total']} | Success: {results['summary']['success']}"
        )
        status_label.config(text="System Scan Complete!")

    def run_full_ecosystem():
        status_label.config(text="Running Full Ecosystem...")
        progress["value"] = 0
        text_area.delete("1.0", "end")

        log("=" * 60, "INFO")
        log("CONCURRENCY MANAGER - Full Ecosystem", "INFO")
        log("=" * 60, "INFO")

        cm.config.max_workers = workers_var.get()
        log(f"Max workers: {cm.config.max_workers}", "INFO")

        results = cm.execute_full_ecosystem()

        log("\nCluster Results:", "INFO")
        for cluster, data in results["clusters"].items():
            status = "OK" if data["success"] else "FAIL"
            log(
                f"  [{status}] {cluster}: {data['duration_ms']:.0f}ms",
                status if data["success"] else "FAIL",
            )

        log("\nSummary:", "INFO")
        log(f"  Total: {results['summary']['total']}", "INFO")
        log(f"  Success: {results['summary']['success']}", "INFO")

        progress["value"] = 100
        status_label.config(text="Ecosystem Complete!")

    def show_status():
        status = cm.get_status()
        text_area.delete("1.0", "end")
        log("=" * 60, "INFO")
        log("CONCURRENCY MANAGER - Status", "INFO")
        log("=" * 60, "INFO")
        log(f"Version: {status['version']}", "INFO")
        log(f"Max Workers: {status['max_workers']}", "INFO")
        log(f"Thread Pool: {status['thread_pool']}", "INFO")
        log(f"Async Enabled: {status['async_enabled']}", "INFO")
        log(f"Clusters: {', '.join(status['clusters_available'])}", "INFO")

    def on_system_scan():
        for btn in [btn_scan, btn_eco, btn_status]:
            btn.config(state="disabled")
        root.update()
        try:
            run_system_scan()
        finally:
            for btn in [btn_scan, btn_eco, btn_status]:
                btn.config(state="normal")

    def on_ecosystem():
        for btn in [btn_scan, btn_eco, btn_status]:
            btn.config(state="disabled")
        root.update()
        try:
            run_full_ecosystem()
        finally:
            for btn in [btn_scan, btn_eco, btn_status]:
                btn.config(state="normal")

    btn_scan = tk.Button(
        button_frame,
        text="SYSTEM SCAN",
        command=on_system_scan,
        font=("Consolas", 13, "bold"),
        bg="#16213e",
        fg="#00ff88",
        width=14,
    )
    btn_scan.pack(side="left", padx=10)

    btn_eco = tk.Button(
        button_frame,
        text="FULL ECOSYSTEM",
        command=on_ecosystem,
        font=("Consolas", 13, "bold"),
        bg="#16213e",
        fg="#00d4ff",
        width=16,
    )
    btn_eco.pack(side="left", padx=10)

    btn_status = tk.Button(
        button_frame,
        text="STATUS",
        command=show_status,
        font=("Consolas", 13, "bold"),
        bg="#16213e",
        fg="#ff00ff",
        width=10,
    )
    btn_status.pack(side="left", padx=10)

    close_btn = tk.Button(
        button_frame,
        text="CLOSE",
        command=root.destroy,
        font=("Consolas", 13, "bold"),
        bg="#ff4444",
        fg="#ffffff",
        width=10,
    )
    close_btn.pack(side="right", padx=10)

    log("CONCURRENCY MANAGER Ready", "INFO")
    log("Configure workers and click SYSTEM SCAN or FULL ECOSYSTEM", "INFO")

    root.mainloop()


def main():
    """Main entry point."""

    parser = argparse.ArgumentParser(
        description="CONCURRENCY MANAGER - Cotemporaneous Execution"
    )
    parser.add_argument("--scan", action="store_true", help="Run system scan")
    parser.add_argument(
        "--ecosystem", action="store_true", help="Run full ecosystem scan"
    )
    parser.add_argument(
        "--workers", type=int, default=8, help="Number of workers (default: 8)"
    )
    parser.add_argument("--status", action="store_true", help="Show status")
    parser.add_argument("--popup", action="store_true", help="Show popup window")

    args = parser.parse_args()

    cm = ConcurrencyManager()
    cm.config.max_workers = args.workers

    if args.popup:
        run_popup()
    elif args.status:
        print(json.dumps(cm.get_status(), indent=2))
    elif args.scan:
        results = cm.execute_system_scan()
        print(json.dumps(results, indent=2, default=str))
    elif args.ecosystem:
        results = cm.execute_full_ecosystem()
        print(json.dumps(results, indent=2, default=str))
    else:
        print("CONCURRENCY MANAGER - Cotemporaneous Execution")
        print()
        print("Usage:")
        print("  python CONCURRENCY.py --scan          Run system scan")
        print("  python CONCURRENCY.py --ecosystem      Run full ecosystem")
        print("  python CONCURRENCY.py --workers 16    set workers")
        print("  python CONCURRENCY.py --status        Show status")
        print("  python CONCURRENCY.py --popup         Show popup")


if __name__ == "__main__":
    main()
