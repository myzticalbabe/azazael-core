# coding: utf-8
"""
TELEMETRYZ - Performance Tracing for Azazael Ecosystem

Deep performance tracing with call stack analysis, flame graph generation,
and comprehensive execution path tracking.

Features:
- Function call tracing
- Call stack recording
- Flame graph generation
- Hot path detection
- Bottleneck identification
- Execution path visualization

Usage:
    >>> from METAWORMS.TELEMETRYZ import TELEMETRYZ
    >>> tz = TELEMETRYZ()
    >>> tz.trace_function(my_function)
    >>> tz.generate_flamegraph()
    >>> tz.get_hot_paths()
"""
import json
import os
import time
import hashlib
import functools
from pathlib import Path
from typing import Callable, Any, List
from dataclasses import dataclass
from collections import defaultdict
import threading
#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# ── Worm Registry ─────────────────────────────────────────────────────
try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("TELEMETRYZ:L37", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls

import tkinter as tk
from tkinter import ttk, scrolledtext
import argparse


# Portability: use env vars with local fallbacks
_PROJECT_ROOT = str(Path(__file__).resolve().parent)
_DEFAULT_OUTPUT = os.path.join(_PROJECT_ROOT, "output")


@dataclass
class TraceEvent:
    """Single trace event."""

    timestamp: float
    function_name: str
    module: str
    duration_ms: float
    call_depth: int
    thread_id: int
    args_hash: str
    return_value_hash: str


@dataclass
class FunctionStats:
    """Function call statistics."""

    function_name: str
    module: str
    call_count: int
    total_time_ms: float
    avg_time_ms: float
    min_time_ms: float
    max_time_ms: float
    call_depth: int


@dataclass
class CallPath:
    """Execution call path."""

    path: list[str]
    total_time_ms: float
    call_count: int


class TELEMETRYZConfig:
    """Configuration for TELEMETRYZ."""

    _OUTPUT_BASE = os.environ.get("AZAZAEL_OUTPUT_PATH", os.path.join(_PROJECT_ROOT, "output", "telemetryz"))
    OUTPUT_PATH = _OUTPUT_BASE
    TRACE_FILE = os.path.join(_OUTPUT_BASE, "trace.json")
    FLAMEGRAPH_FILE = os.path.join(_OUTPUT_BASE, "flamegraph.html")
    HOTPATHS_FILE = os.path.join(_OUTPUT_BASE, "hotpaths.json")

    MAX_TRACE_EVENTS = 100000
    SAMPLE_RATE = 1.0
    TRACED_MODULES: set[str] = set()

    TRACED_PATTERNS = [
        "WORMZ",
        "METAWORMS",
        "TERENCE",
        "HYDRA",
        "SYSTEMZ",
        "DISKZ",
        "PROCESSZ",
    ]


@register_worm(category="profiling", priority=6, cooldown_seconds=60.0, description="Performance tracing system — flame graphs, hot path detection, bottleneck identification")
class TELEMETRYZ:
    """Performance Tracing System."""

    VERSION = "1.0.0"

    def __init__(self):
        self.config = TELEMETRYZConfig()
        self.output_path = Path(self.config.OUTPUT_PATH)
        self._ensure_directories()

        self.trace_events: list[TraceEvent] = []
        self.call_stack: list[str] = []
        self.call_times: dict[str, float] = {}
        self.active_traces: dict[int, list[TraceEvent]] = defaultdict(list)

        self._lock = threading.Lock()
        self._tracing_enabled = False
        self._trace_depth = 0
        self._max_depth = 50

        self._wrapped_functions: dict[Callable, Callable] = {}

    def _ensure_directories(self):
        """Create output directories."""
        self.output_path.mkdir(parents=True, exist_ok=True)

    def trace_function(self, func: Callable) -> Callable:
        """Wrap function with tracing."""

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return self._traced_call(func, args, kwargs)

        self._wrapped_functions[func] = wrapper
        return wrapper

    def _traced_call(
        self,
        func: Callable,
        args: tuple,
        kwargs: dict,
    ) -> Any:
        """Execute traced function call."""
        if not self._tracing_enabled:
            return func(*args, **kwargs)

        self._trace_depth += 1
        if self._trace_depth > self._max_depth:
            self._trace_depth -= 1
            return func(*args, **kwargs)

        thread_id = threading.current_thread().ident
        func_name = func.__name__
        module = func.__module__

        args_hash = hashlib.md5(str(args).encode()).hexdigest()[:8]

        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            return_value_hash = hashlib.md5(str(result).encode()).hexdigest()[:8]
        except Exception as e:
            return_value_hash = ""
            str(e)
            raise
        finally:
            duration_ms = (time.time() - start_time) * 1000
            self._trace_depth -= 1

        event = TraceEvent(
            timestamp=start_time,
            function_name=func_name,
            module=module,
            duration_ms=duration_ms,
            call_depth=len(self.call_stack),
            thread_id=thread_id or 0,
            args_hash=args_hash,
            return_value_hash=return_value_hash,
        )

        with self._lock:
            self.trace_events.append(event)
            if len(self.trace_events) > self.config.MAX_TRACE_EVENTS:
                self.trace_events = self.trace_events[-self.config.MAX_TRACE_EVENTS :]

        return result

    def start_trace(self):
        """Enable tracing."""
        self._tracing_enabled = True
        self.trace_events = []

    def stop_trace(self) -> int:
        """Disable tracing."""
        self._tracing_enabled = False
        return len(self.trace_events)

    def clear_trace(self):
        """Clear trace data."""
        with self._lock:
            self.trace_events = []
            self.call_stack = []
            self.call_times = {}

    def get_function_stats(self) -> list[FunctionStats]:
        """Calculate function statistics."""
        func_data: dict[str, dict] = defaultdict(
            lambda: {
                "call_count": 0,
                "total_time": 0.0,
                "min_time": float("inf"),
                "max_time": 0.0,
                "depths": set(),
            }
        )

        for event in self.trace_events:
            key = f"{event.module}.{event.function_name}"
            data = func_data[key]
            data["call_count"] += 1
            data["total_time"] += event.duration_ms
            data["min_time"] = min(data["min_time"], event.duration_ms)
            data["max_time"] = max(data["max_time"], event.duration_ms)
            data["depths"].add(event.call_depth)

        stats = []
        for name, data in func_data.items():
            parts = name.rsplit(".", 1)
            module = parts[0] if len(parts) > 1 else ""
            func_name = parts[-1] if parts else name
            avg = data["total_time"] / max(data["call_count"], 1)
            stats.append(
                FunctionStats(
                    function_name=func_name,
                    module=module,
                    call_count=data["call_count"],
                    total_time_ms=round(data["total_time"], 2),
                    avg_time_ms=round(avg, 2),
                    min_time_ms=round(data["min_time"], 2)
                    if data["min_time"] != float("inf")
                    else 0,
                    max_time_ms=round(data["max_time"], 2),
                    call_depth=min(data["depths"]) if data["depths"] else 0,
                )
            )

        return sorted(stats, key=lambda x: -x.total_time_ms)

    def get_hot_paths(self, min_calls: int = 3) -> list[CallPath]:
        """Extract hot execution paths."""
        paths: dict[str, CallPath] = {}

        current_path: list[str] = []
        for event in self.trace_events:
            if event.call_depth == 0:
                if current_path:
                    path_key = " -> ".join(current_path)
                    if path_key not in paths:
                        paths[path_key] = CallPath(
                            path=current_path.copy(),
                            total_time_ms=0,
                            call_count=0,
                        )
                    paths[path_key].total_time_ms += event.duration_ms
                    paths[path_key].call_count += 1
                current_path = []
            func_name = f"{event.module}.{event.function_name}"
            current_path.append(func_name)

        hot = [p for p in paths.values() if p.call_count >= min_calls]
        return sorted(hot, key=lambda x: -x.total_time_ms)[:20]

    def identify_bottlenecks(self, percentile: float = 95) -> list[dict]:
        """Identify performance bottlenecks."""
        stats = self.get_function_stats()

        threshold = 0
        if stats:
            durations = [s.total_time_ms for s in stats]
            threshold = sorted(durations)[int(len(durations) * percentile / 100)]

        bottlenecks = [
            {
                "function": s.function_name,
                "module": s.module,
                "total_time_ms": s.total_time_ms,
                "avg_time_ms": s.avg_time_ms,
                "call_count": s.call_count,
                "recommendation": "Consider optimization or caching"
                if s.avg_time_ms > 100
                else "Monitor for degradation",
            }
            for s in stats
            if s.total_time_ms >= threshold
        ]

        return sorted(bottlenecks, key=lambda x: -x["total_time_ms"])[:20]

    def generate_flamegraph(self, output_path: (str | None) = None) -> str:
        """Generate flame graph HTML."""
        func_times: dict[str, float] = defaultdict(float)

        for event in self.trace_events:
            key = f"{event.module}.{event.function_name}"
            func_times[key] += event.duration_ms

        sorted_funcs = sorted(func_times.items(), key=lambda x: -x[1])
        max_time = sorted_funcs[0][1] if sorted_funcs else 1

        html = """<!DOCTYPE html>
<html>
<head>
    <title>Azazael Flame Graph</title>
    <style>
        body { font-family: monospace; margin: 20px; background: #1a1a2e; color: #eee; }
        .frame { display: inline-block; margin: 2px 0; }
        .frame span { padding: 5px 10px; display: inline-block; }
        .title { font-size: 24px; margin-bottom: 20px; color: #00d9ff; }
        .legend { margin-bottom: 20px; color: #888; }
    </style>
</head>
<body>
    <div class="title">Azazael Performance Flame Graph</div>
    <div class="legend">Width = Total Time | Hover for details</div>
"""

        for func, duration in sorted_funcs[:800]:
            width = max(1, int((duration / max_time) * 100))
            hue = int(200 - (duration / max_time) * 100)
            html += f'<div class="frame"><span style="background: hsl({hue}, 70%, 40%); width: {width}px;" title="{func}: {duration:.2f}ms">{func.split(".")[-1]}</span></div>\n'

        html += "</body></html>"

        path = output_path or self.config.FLAMEGRAPH_FILE
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

        return path

    def trace_decorator(self, module_pattern: str = ""):
        """Decorator to auto-trace module functions."""

        def decorator(cls):
            for attr_name in dir(cls):
                if not attr_name.startswith("_"):
                    attr = getattr(cls, attr_name)
                    if callable(attr):
                        setattr(cls, attr_name, self.trace_function(attr))
            return cls

        return decorator

    def trace_module(self, module_name: str):
        """Trace all functions in a module."""
        try:
            import sys

            if module_name in sys.modules:
                module = sys.modules[module_name]
                for attr_name in dir(module):
                    if not attr_name.startswith("_"):
                        attr = getattr(module, attr_name)
                        if callable(attr):
                            setattr(module, attr_name, self.trace_function(attr))
        except Exception as e:
            print(f"Failed to trace module {module_name}: {e}")

    def analyze_execution_flow(self) -> dict[str, Any]:
        """Analyze execution flow patterns."""
        if not self.trace_events:
            return {"message": "No trace data"}

        start_times = sorted(set(e.timestamp for e in self.trace_events))
        end_time = max(e.timestamp + e.duration_ms / 1000 for e in self.trace_events)
        duration = end_time - min(start_times) if start_times else 0

        unique_functions = len(
            set(f"{e.module}.{e.function_name}" for e in self.trace_events)
        )
        threads = len(set(e.thread_id for e in self.trace_events))

        return {
            "duration_seconds": round(duration, 3),
            "total_events": len(self.trace_events),
            "unique_functions": unique_functions,
            "threads": threads,
            "events_per_second": round(
                len(self.trace_events) / max(duration, 0.001), 2
            ),
            "avg_event_rate": round(len(self.trace_events) / max(duration, 0.001), 2)
            if duration
            else 0,
        }

    def save_trace(self, path: (str | None) = None):
        """Save trace to file."""
        path = path or self.config.TRACE_FILE
        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {
                        "timestamp": e.timestamp,
                        "function": e.function_name,
                        "module": e.module,
                        "duration_ms": e.duration_ms,
                        "depth": e.call_depth,
                    }
                    for e in self.trace_events
                ],
                f,
                indent=2,
            )

    def load_trace(self, path: str):
        """Load trace from file."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            self.trace_events = [
                TraceEvent(
                    timestamp=d["timestamp"],
                    function_name=d["function"],
                    module=d.get("module", ""),
                    duration_ms=d["duration_ms"],
                    call_depth=d.get("depth", 0),
                    thread_id=0,
                    args_hash="",
                    return_value_hash="",
                )
                for d in data
            ]

    def get_status(self) -> dict[str, Any]:
        """Get tracing status."""
        return {
            "version": self.VERSION,
            "tracing_enabled": self._tracing_enabled,
            "events_captured": len(self.trace_events),
            "functions_wrapped": len(self._wrapped_functions),
            "max_events": self.config.MAX_TRACE_EVENTS,
        }

    def run_popup(self):
        """Run with GUI popup."""
        root = tk.Tk()
        root.title("TELEMETRYZ - Performance Tracing")
        root.geometry("900x600")

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        stats_tab = ttk.Frame(notebook)
        notebook.add(stats_tab, text="Function Stats")

        hotpaths_tab = ttk.Frame(notebook)
        notebook.add(hotpaths_tab, text="Hot Paths")

        bottlenecks_tab = ttk.Frame(notebook)
        notebook.add(bottlenecks_tab, text="Bottlenecks")

        stats_text = scrolledtext.ScrolledText(stats_tab, width=100, height=25)
        stats_text.pack(fill="both", expand=True, padx=10, pady=10)

        hot_text = scrolledtext.ScrolledText(hotpaths_tab, width=100, height=25)
        hot_text.pack(fill="both", expand=True, padx=10, pady=10)

        bottle_text = scrolledtext.ScrolledText(bottlenecks_tab, width=100, height=25)
        bottle_text.pack(fill="both", expand=True, padx=10, pady=10)

        def refresh():
            stats = self.get_function_stats()
            stats_text.delete("1.0", "end")
            stats_text.insert(
                "end",
                json.dumps(
                    [
                        {
                            "function": s.function_name,
                            "total_ms": s.total_time_ms,
                            "avg_ms": s.avg_time_ms,
                            "calls": s.call_count,
                        }
                        for s in stats[:30]
                    ],
                    indent=2,
                ),
            )

            hot = self.get_hot_paths()
            hot_text.delete("1.0", "end")
            hot_text.insert(
                "end",
                json.dumps(
                    [
                        {
                            "path": p.path[:5],
                            "total_ms": p.total_time_ms,
                            "calls": p.call_count,
                        }
                        for p in hot[:20]
                    ],
                    indent=2,
                ),
            )

            bottlenecks = self.identify_bottlenecks()
            bottle_text.delete("1.0", "end")
            bottle_text.insert("end", json.dumps(bottlenecks[:20], indent=2))

        def start():
            self.start_trace()

        def stop():
            events = self.stop_trace()
            status_label.config(text=f"Captured {events} events")
            self.generate_flamegraph()
            refresh()

        status_label = ttk.Label(stats_tab, text="Ready", font=("Arial", 12))
        status_label.pack(pady=10)

        ttk.Button(stats_tab, text="Start Trace", command=start).pack(pady=5)
        ttk.Button(stats_tab, text="Stop & Analyze", command=stop).pack(pady=5)
        ttk.Button(stats_tab, text="Refresh", command=refresh).pack(pady=5)
        ttk.Button(stats_tab, text="Clear", command=self.clear_trace).pack(pady=5)

        root.mainloop()


def main():
    """CLI entry point."""

    parser = argparse.ArgumentParser(description="TELEMETRYZ - Performance Tracing")
    parser.add_argument("--version", action="version", version="1.0.0")
    parser.add_argument("--stats", action="store_true", help="Show function stats")
    parser.add_argument("--hotpaths", action="store_true", help="Show hot paths")
    parser.add_argument(
        "--bottlenecks", action="store_true", help="Identify bottlenecks"
    )
    parser.add_argument(
        "--flamegraph", action="store_true", help="Generate flame graph"
    )
    parser.add_argument("--flow", action="store_true", help="Analyze execution flow")
    parser.add_argument("--start", action="store_true", help="Start tracing")
    parser.add_argument("--stop", action="store_true", help="Stop tracing")
    parser.add_argument("--clear", action="store_true", help="Clear trace")
    parser.add_argument("--save", help="Save trace to file")
    parser.add_argument("--load", help="Load trace from file")
    parser.add_argument("--popup", action="store_true", help="Launch GUI")

    args = parser.parse_args()
    tz = TELEMETRYZ()

    if args.stats:
        print(
            json.dumps(
                [
                    {
                        "function": s.function_name,
                        "total_ms": s.total_time_ms,
                        "avg_ms": s.avg_time_ms,
                        "calls": s.call_count,
                    }
                    for s in tz.get_function_stats()[:30]
                ],
                indent=2,
            )
        )
    elif args.hotpaths:
        print(
            json.dumps(
                [
                    {
                        "path": p.path[:5],
                        "total_ms": p.total_time_ms,
                        "calls": p.call_count,
                    }
                    for p in tz.get_hot_paths()[:20]
                ],
                indent=2,
            )
        )
    elif args.bottlenecks:
        print(json.dumps(tz.identify_bottlenecks()[:20], indent=2))
    elif args.flamegraph:
        path = tz.generate_flamegraph()
        print(f"Flame graph saved: {path}")
    elif args.flow:
        print(json.dumps(tz.analyze_execution_flow(), indent=2))
    elif args.start:
        tz.start_trace()
        print("Tracing started")
    elif args.stop:
        events = tz.stop_trace()
        print(f"Tracing stopped: {events} events")
    elif args.clear:
        tz.clear_trace()
        print("Trace cleared")
    elif args.save:
        tz.save_trace(args.save)
        print(f"Trace saved: {args.save}")
    elif args.load:
        tz.load_trace(args.load)
        print(f"Trace loaded: {args.load}")
    elif args.popup:
        tz.run_popup()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
