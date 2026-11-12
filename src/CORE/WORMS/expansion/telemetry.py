# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: TELEMETRY (5 worms)
SpanTracer, MetricAggregator, LogStreamer, EventRouter, AlertEscalator
"""

import time
import hashlib
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, List
from collections import defaultdict, deque
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
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("telemetry:L19", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls

# -- Python 3.14t Free-Threading Awareness ----------------------------
try:
    from CORE.freethreading import is_free_threaded, FreeThreadPool, ThreadSafeDict, ThreadSafeList
    _FREETHREAD = True
except ImportError as exc:
    note_failure("telemetry:L28", exc)
    _FREETHREAD = False
    FreeThreadPool = None
    ThreadSafeDict = dict
    ThreadSafeList = list
    def is_free_threaded() -> bool:
        try:
            return bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
        except Exception as exc:
            note_failure("telemetry:L37", exc)
            return False


@register_worm(category="telemetry", priority=7, cooldown_seconds=30.0,
    description="Span tracer — OpenTelemetry-compatible span creation, trace context propagation")
class SpanTracer:
    """Distributed span tracer for worm execution tracing."""

    VERSION = "1.0.0"

    def __init__(self):
        self._spans: list[dict] = []
        self._lock = threading.Lock()
        self._trace_id = hashlib.sha256(str(time.time()).encode()).hexdigest()[:32]

    def start_span(self, name: str, parent_id: str = "") -> dict[str, Any]:
        """Start a new trace span."""
        span = {
            "id": hashlib.sha256(f"{name}{time.time()}".encode()).hexdigest()[:16],
            "trace_id": self._trace_id, "name": name, "parent_id": parent_id,
            "start_time": time.time(), "end_time": None, "duration_ms": 0, "status": "running",
        }
        with self._lock:
            self._spans.append(span)
        return span

    def end_span(self, span_id: str, status: str = "ok") -> (dict | None):
        """End a trace span."""
        with self._lock:
            for s in self._spans:
                if s["id"] == span_id:
                    s["end_time"] = time.time()
                    s["duration_ms"] = round((s["end_time"] - s["start_time"]) * 1000, 2)
                    s["status"] = status
                    return s
        return None

    def get_trace_tree(self) -> dict[str, Any]:
        """Get the trace as a tree structure."""
        with self._lock:
            return {"trace_id": self._trace_id, "span_count": len(self._spans), "spans": self._spans[-100:]}


@register_worm(category="telemetry", priority=6, cooldown_seconds=30.0,
    description="Metric aggregator — Prometheus-style counter/gauge/histogram with time-window aggregation")
class MetricAggregator:
    """Aggregates metrics with Prometheus-style types and time-window summarization."""

    VERSION = "1.0.0"

    def __init__(self):
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def inc_counter(self, name: str, value: float = 1.0):
        with self._lock:
            self._counters[name] += value

    def set_gauge(self, name: str, value: float):
        with self._lock:
            self._gauges[name] = value

    def observe(self, name: str, value: float):
        with self._lock:
            self._histograms[name].append(value)
            if len(self._histograms[name]) > 10000:
                self._histograms[name] = self._histograms[name][-5000:]

    def get_snapshot(self) -> dict[str, Any]:
        """Get Prometheus-style text snapshot."""
        with self._lock:
            return {
                "counters": dict(self._counters), "gauges": dict(self._gauges),
                "histograms": {k: {"count": len(v), "avg": round(sum(v) / len(v), 2) if v else 0,
                                   "p95": sorted(v)[int(len(v) * 0.95)] if v else 0} for k, v in self._histograms.items()},
            }


@register_worm(category="telemetry", priority=5, cooldown_seconds=15.0,
    description="Log streamer — real-time log tailing, pattern filtering, structured log parsing")
class LogStreamer:
    """Streams and filters log files in real-time."""

    VERSION = "1.0.0"

    def __init__(self):
        self._watchers: dict[str, dict] = {}
        self._lock = threading.Lock()

    def watch_file(self, filepath: str, pattern: str = "") -> str:
        """Start watching a log file."""
        watch_id = f"watch_{len(self._watchers)}"
        path = Path(filepath)
        pos = path.stat().st_size if path.exists() else 0
        with self._lock:
            self._watchers[watch_id] = {"path": str(path), "position": pos, "pattern": pattern, "last_read": time.time()}
        return watch_id

    def read_new_lines(self, watch_id: str, max_lines: int = 100) -> list[str]:
        """Read new lines from a watched file."""
        with self._lock:
            w = self._watchers.get(watch_id)
            if not w:
                return []
        path = Path(w["path"])
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(w["position"])
            lines = f.readlines()
            w["position"] = f.tell()
        if w["pattern"]:
            import re
            lines = [l for l in lines if re.search(w["pattern"], l)]
        return [l.rstrip() for l in lines[-max_lines:]]

    def get_summary(self):
        return {"status":"ok","worm":"LogStreamer"}


@register_worm(category="telemetry", priority=6, cooldown_seconds=15.0,
    description="Event router — pub/sub event bus with topic-based routing and subscriber callbacks")
class EventRouter:
    """Pub/sub event bus with topic-based routing."""

    VERSION = "1.0.0"

    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._event_log: deque = deque(maxlen=1000)
        self._lock = threading.Lock()

    def subscribe(self, topic: str, callback: Callable):
        with self._lock:
            self._subscribers[topic].append(callback)

    def publish(self, topic: str, data: Any):
        event = {"topic": topic, "data": data, "timestamp": time.time()}
        with self._lock:
            self._event_log.append(event)
            callbacks = list(self._subscribers.get(topic, [])) + list(self._subscribers.get("*", []))
        for cb in callbacks:
            try:
                cb(event)
            except Exception as exc:
                note_failure("telemetry:L184", exc)

    def get_recent_events(self, topic: str = "", limit: int = 50) -> list[dict]:
        with self._lock:
            events = [e for e in self._event_log if not topic or e["topic"] == topic]
            return list(events)[-limit:]

    def get_summary(self):
        return {"status":"ok","worm":"EventRouter"}


@register_worm(category="telemetry", priority=5, cooldown_seconds=60.0,
    description="Alert escalator — threshold-based alerting with severity levels and deduplication")
class AlertEscalator:
    """Threshold-based alerting with severity escalation and deduplication."""

    VERSION = "1.0.0"

    def __init__(self):
        self._alerts: list[dict] = []
        self._fired: set[str] = set()
        self._lock = threading.Lock()

    def check_threshold(self, name: str, value: float, warn: float, crit: float) -> (dict | None):
        """Check a value against thresholds and fire alert if needed."""
        dedup = f"{name}:{warn}:{crit}"
        if value >= crit and dedup not in self._fired:
            alert = {"name": name, "value": value, "severity": "CRITICAL", "threshold": crit, "time": datetime.now().isoformat()}
            with self._lock:
                self._fired.add(dedup)
                self._alerts.append(alert)
            return alert
        elif value >= warn:
            return {"name": name, "value": value, "severity": "WARNING", "threshold": warn}
        return None

    def clear_alert(self, name: str):
        with self._lock:
            to_remove = [k for k in self._fired if k.startswith(f"{name}:")]
            for k in to_remove:
                self._fired.discard(k)


    def get_summary(self):
        return {"status":"ok","worm":"AlertEscalator","method":"check_threshold"}