# coding: utf-8
"""
METRICZ - Execution Telemetry for Azazael Ecosystem

Structured execution telemetry with metrics collection, performance tracking,
and comprehensive observability for the Azazael worm ecosystem.

Features:
- Execution metrics collection
- Performance tracing
- Resource utilization tracking
- Success/failure analytics
- Time series storage
- Alert generation

Usage:
    >>> from METAWORMS.METRICZ import METRICZ
    >>> mz = METRICZ()
    >>> mz.record_execution("WORMZ", duration_ms=1500, success=True)
    >>> mz.get_metrics()
    >>> mz.generate_report()
"""

import os
import json
import time
import hashlib
import statistics
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass, field, asdict
from collections import defaultdict, deque
from contextlib import contextmanager

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
    note_failure("METRICZ:L41", exc)
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
class MetricPoint:
    """Single metric data point."""

    timestamp: str
    metric: str
    value: float
    tags: dict[str, str] = field(default_factory=dict)
    source: str = ""


@dataclass
class ExecutionRecord:
    """Execution telemetry record."""

    execution_id: str
    component: str
    action: str
    start_time: str
    end_time: str
    duration_ms: float
    success: bool
    error: (str | None) = None
    metrics: dict[str, float] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class PerformanceSummary:
    """Performance summary statistics."""

    component: str
    total_executions: int
    success_count: int
    failure_count: int
    avg_duration_ms: float
    min_duration_ms: float
    max_duration_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    throughput_per_min: float


class METRICZConfig:
    """Configuration for METRICZ."""

    _OUTPUT_BASE = os.environ.get("AZAZAEL_OUTPUT_PATH", os.path.join(_PROJECT_ROOT, "output", "metricz"))
    OUTPUT_PATH = _OUTPUT_BASE
    METRICS_FILE = os.path.join(_OUTPUT_BASE, "metrics.json")
    EXECUTIONS_FILE = os.path.join(_OUTPUT_BASE, "executions.json")
    ALERTS_FILE = os.path.join(_OUTPUT_BASE, "alerts.json")

    HISTORY_LENGTH = 10000
    ALERT_THRESHOLDS = {
        "duration_ms": {"warning": 5000, "critical": 10000},
        "failure_rate": {"warning": 0.1, "critical": 0.3},
        "error_rate": {"warning": 0.05, "critical": 0.15},
    }

    SAMPLE_RATES = {
        "cpu_percent": 1.0,
        "memory_percent": 1.0,
        "disk_percent": 5.0,
    }


@register_worm(category="monitor", priority=5, cooldown_seconds=30.0, description="Execution telemetry collector — metrics, performance tracking, alerting")
class METRICZ:
    """Execution Telemetry Collector."""

    VERSION = "1.0.0"

    def __init__(self):
        self.config = METRICZConfig()
        self.output_path = Path(self.config.OUTPUT_PATH)
        self._ensure_directories()

        self.metrics_buffer: deque = deque(maxlen=self.config.HISTORY_LENGTH)
        self.executions: list[ExecutionRecord] = []
        self.alerts: list[dict] = []
        self.active_timers: dict[str, float] = {}

        self._lock = threading.Lock()
        self._execution_counter = 0

        self._load_data()

    def _ensure_directories(self):
        """Create output directories."""
        self.output_path.mkdir(parents=True, exist_ok=True)

    def record_metric(
        self,
        metric: str,
        value: float,
        tags: (dict[str, str] | None) = None,
        source: str = "",
    ):
        """Record a metric data point."""
        point = MetricPoint(
            timestamp=datetime.now().isoformat(),
            metric=metric,
            value=value,
            tags=tags or {},
            source=source,
        )
        with self._lock:
            self.metrics_buffer.append(point)
            self._save_metrics()

    def record_execution(
        self,
        component: str,
        action: str,
        duration_ms: float,
        success: bool,
        error: (str | None) = None,
        metrics: (dict[str, float] | None) = None,
        tags: (dict[str, str] | None) = None,
    ):
        """Record execution telemetry."""
        self._execution_counter += 1
        execution_id = hashlib.md5(
            f"{time.time()}_{self._execution_counter}".encode()
        ).hexdigest()[:12]

        record = ExecutionRecord(
            execution_id=execution_id,
            component=component,
            action=action,
            start_time=datetime.now().isoformat(),
            end_time=datetime.now().isoformat(),
            duration_ms=duration_ms,
            success=success,
            error=error,
            metrics=metrics or {},
            tags=tags or {},
        )

        with self._lock:
            self.executions.append(record)
            if len(self.executions) > self.config.HISTORY_LENGTH:
                self.executions = self.executions[-self.config.HISTORY_LENGTH :]
            self._save_executions()

        self._check_thresholds(record)

    @contextmanager
    def timer(self, component: str, action: str, tags: (dict[str, str] | None) = None):
        """Context manager for timing executions."""
        start_time = time.time()
        timer_id = f"{component}_{action}_{start_time}"
        self.active_timers[timer_id] = start_time
        try:
            yield
            success = True
            error = None
        except Exception as e:
            success = False
            error = str(e)
            raise
        finally:
            duration_ms = (time.time() - start_time) * 1000
            del self.active_timers[timer_id]
            self.record_execution(
                component=component,
                action=action,
                duration_ms=duration_ms,
                success=success,
                error=error,
                tags=tags,
            )

    def start_timer(self, component: str, action: str) -> str:
        """Start a named timer."""
        timer_id = f"{component}_{action}_{time.time()}"
        self.active_timers[timer_id] = time.time()
        return timer_id

    def stop_timer(
        self,
        timer_id: str,
        success: bool = True,
        error: (str | None) = None,
    ) -> float:
        """Stop a named timer and record execution."""
        if timer_id not in self.active_timers:
            return 0.0

        start_time = self.active_timers[timer_id]
        duration_ms = (time.time() - start_time) * 1000
        del self.active_timers[timer_id]

        parts = timer_id.split("_")
        component = parts[0] if parts else "Unknown"
        action = parts[1] if len(parts) > 1 else "timer"

        self.record_execution(
            component=component,
            action=action,
            duration_ms=duration_ms,
            success=success,
            error=error,
        )

        return duration_ms

    def _check_thresholds(self, record: ExecutionRecord):
        """Check alerts thresholds."""
        thresholds = self.config.ALERT_THRESHOLDS

        if record.duration_ms >= thresholds.get("duration_ms", {}).get(
            "critical", 10000
        ):
            self._create_alert(
                "duration",
                "CRITICAL",
                f"{record.component}/{record.action} took {record.duration_ms:.0f}ms",
                "Consider optimizing or investigating",
            )
        elif record.duration_ms >= thresholds.get("duration_ms", {}).get(
            "warning", 5000
        ):
            self._create_alert(
                "duration",
                "WARNING",
                f"{record.component}/{record.action} took {record.duration_ms:.0f}ms",
                "Monitor for degradation",
            )

    def _create_alert(
        self, alert_type: str, severity: str, message: str, recommendation: str
    ):
        """Create an alert."""
        alert = {
            "timestamp": datetime.now().isoformat(),
            "type": alert_type,
            "severity": severity,
            "message": message,
            "recommendation": recommendation,
        }
        with self._lock:
            self.alerts.append(alert)
            if len(self.alerts) > 1000:
                self.alerts = self.alerts[-1000:]
            self._save_alerts()

    def get_metrics(
        self,
        metric: (str | None) = None,
        hours: int = 24,
        limit: int = 1000,
    ) -> list[dict]:
        """Get filtered metrics."""
        cutoff = datetime.now() - timedelta(hours=hours)
        results = []

        with self._lock:
            for point in list(self.metrics_buffer)[-limit:]:
                try:
                    ts = datetime.fromisoformat(point.timestamp)
                    if ts >= cutoff:
                        if metric is None or point.metric == metric:
                            results.append(
                                {
                                    "timestamp": point.timestamp,
                                    "metric": point.metric,
                                    "value": point.value,
                                    "tags": point.tags,
                                    "source": point.source,
                                }
                            )
                except Exception as exc:
                    note_failure("METRICZ:L327", exc)

        return results

    def get_performance_summary(
        self, component: (str | None) = None
    ) -> list[PerformanceSummary]:
        """Get performance summary by component."""
        with self._lock:
            records = self.executions

        if component:
            records = [r for r in records if r.component == component]

        by_component = defaultdict(list)
        for r in records:
            by_component[r.component].append(r)

        summaries = []
        for comp, comp_records in by_component.items():
            durations = [r.duration_ms for r in comp_records]
            success_count = sum(1 for r in comp_records if r.success)
            failure_count = len(comp_records) - success_count

            sorted_durations = sorted(durations)
            n = len(sorted_durations)

            p50 = sorted_durations[int(n * 0.50)] if n > 0 else 0
            p95 = sorted_durations[int(n * 0.95)] if n > 0 else 0
            p99 = sorted_durations[int(n * 0.99)] if n > 0 else 0

            time_span = 0
            if len(comp_records) >= 2:
                try:
                    first = datetime.fromisoformat(comp_records[0].start_time)
                    last = datetime.fromisoformat(comp_records[-1].start_time)
                    time_span = (last - first).total_seconds()
                except Exception as exc:
                    note_failure("METRICZ:L365", exc)
                    time_span = 1

            throughput = len(comp_records) / max(time_span / 60, 1)

            summaries.append(
                PerformanceSummary(
                    component=comp,
                    total_executions=len(comp_records),
                    success_count=success_count,
                    failure_count=failure_count,
                    avg_duration_ms=statistics.mean(durations) if durations else 0,
                    min_duration_ms=min(durations) if durations else 0,
                    max_duration_ms=max(durations) if durations else 0,
                    p50_ms=p50,
                    p95_ms=p95,
                    p99_ms=p99,
                    throughput_per_min=throughput,
                )
            )

        return summaries

    def get_recent_executions(self, limit: int = 50) -> list[dict]:
        """Get recent executions."""
        with self._lock:
            recent = self.executions[-limit:]
        return [
            {
                "execution_id": r.execution_id,
                "component": r.component,
                "action": r.action,
                "duration_ms": r.duration_ms,
                "success": r.success,
                "error": r.error,
                "timestamp": r.start_time,
            }
            for r in reversed(recent)
        ]

    def get_alerts(self, limit: int = 50) -> list[dict]:
        """Get recent alerts."""
        with self._lock:
            return list(self.alerts[-limit:])

    def get_summary(self) -> dict[str, Any]:
        """Get telemetry summary."""
        with self._lock:
            total_metrics = len(self.metrics_buffer)
            total_executions = len(self.executions)
            active_timers = len(self.active_timers)

        performance = self.get_performance_summary()
        total_failures = sum(p.failure_count for p in performance)

        return {
            "version": self.VERSION,
            "timestamp": datetime.now().isoformat(),
            "metrics_collected": total_metrics,
            "executions_recorded": total_executions,
            "active_timers": active_timers,
            "alerts_count": len(self.alerts),
            "failure_rate": total_failures / max(total_executions, 1),
            "components_monitored": len(performance),
        }

    def generate_report(self, hours: int = 24) -> dict[str, Any]:
        """Generate comprehensive telemetry report."""
        cutoff = datetime.now() - timedelta(hours=hours)

        with self._lock:
            recent_metrics = [
                p
                for p in self.metrics_buffer
                if datetime.fromisoformat(p.timestamp) >= cutoff
            ]
            recent_executions = [
                r
                for r in self.executions
                if datetime.fromisoformat(r.start_time) >= cutoff
            ]

        performance = self.get_performance_summary()
        alerts = self.get_alerts()

        return {
            "report_time": datetime.now().isoformat(),
            "period_hours": hours,
            "summary": {
                "metrics_count": len(recent_metrics),
                "executions_count": len(recent_executions),
                "success_rate": sum(1 for r in recent_executions if r.success)
                / max(len(recent_executions), 1),
                "alerts_count": len(alerts),
            },
            "performance": [
                {
                    "component": p.component,
                    "executions": p.total_executions,
                    "success_rate": p.success_count / max(p.total_executions, 1),
                    "avg_duration_ms": round(p.avg_duration_ms, 2),
                    "p95_ms": round(p.p95_ms, 2),
                    "throughput_per_min": round(p.throughput_per_min, 2),
                }
                for p in performance
            ],
            "recent_alerts": alerts[-10:],
        }

    def export_metrics(self, path: str, format: str = "json") -> bool:
        """Export metrics to file."""
        metrics = self.get_metrics(limit=self.config.HISTORY_LENGTH)
        try:
            with open(path, "w", encoding="utf-8") as f:
                if format == "json":
                    json.dump(metrics, f, indent=2)
            return True
        except Exception as exc:
            note_failure("METRICZ:L482", exc)
            return False

    def _save_metrics(self):
        """Persist metrics buffer."""
        metrics_file = Path(self.config.METRICS_FILE)
        metrics_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(metrics_file, "w", encoding="utf-8") as f:
                json.dump(
                    [
                        {
                            "timestamp": p.timestamp,
                            "metric": p.metric,
                            "value": p.value,
                            "tags": p.tags,
                            "source": p.source,
                        }
                        for p in list(self.metrics_buffer)[-1000:]
                    ],
                    f,
                    indent=2,
                )
        except Exception as exc:
            note_failure("METRICZ:L505", exc)

    def _save_executions(self):
        """Persist executions."""
        executions_file = Path(self.config.EXECUTIONS_FILE)
        executions_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(executions_file, "w", encoding="utf-8") as f:
                json.dump(
                    [
                        {
                            "execution_id": r.execution_id,
                            "component": r.component,
                            "action": r.action,
                            "start_time": r.start_time,
                            "end_time": r.end_time,
                            "duration_ms": r.duration_ms,
                            "success": r.success,
                            "error": r.error,
                            "metrics": r.metrics,
                            "tags": r.tags,
                        }
                        for r in self.executions[-1000:]
                    ],
                    f,
                    indent=2,
                )
        except Exception as exc:
            note_failure("METRICZ:L533", exc)

    def _save_alerts(self):
        """Persist alerts."""
        alerts_file = Path(self.config.ALERTS_FILE)
        alerts_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(alerts_file, "w", encoding="utf-8") as f:
                json.dump(self.alerts[-1000:], f, indent=2)
        except Exception as exc:
            note_failure("METRICZ:L543", exc)

    def _load_data(self):
        """Load persisted data."""
        metrics_file = Path(self.config.METRICS_FILE)
        if metrics_file.exists():
            try:
                with open(metrics_file, encoding="utf-8") as f:
                    data = json.load(f)
                    for d in data:
                        self.metrics_buffer.append(
                            MetricPoint(
                                timestamp=d["timestamp"],
                                metric=d["metric"],
                                value=d["value"],
                                tags=d.get("tags", {}),
                                source=d.get("source", ""),
                            )
                        )
            except Exception as exc:
                note_failure("METRICZ:L563", exc)

    def run_popup(self):
        """Run with GUI popup."""
        root = tk.Tk()
        root.title("METRICZ - Execution Telemetry")
        root.geometry("900x600")

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        summary_tab = ttk.Frame(notebook)
        notebook.add(summary_tab, text="Summary")

        executions_tab = ttk.Frame(notebook)
        notebook.add(executions_tab, text="Executions")

        alerts_tab = ttk.Frame(notebook)
        notebook.add(alerts_tab, text="Alerts")

        summary_label = ttk.Label(summary_tab, text="Loading...", font=("Arial", 12))
        summary_label.pack(pady=10)

        exec_text = scrolledtext.ScrolledText(executions_tab, width=100, height=25)
        exec_text.pack(fill="both", expand=True, padx=10, pady=10)

        alert_text = scrolledtext.ScrolledText(alerts_tab, width=100, height=25)
        alert_text.pack(fill="both", expand=True, padx=10, pady=10)

        def refresh():
            summary = self.get_summary()
            summary_text = f"""METRICZ Telemetry Summary

Metrics Collected: {summary["metrics_collected"]}
Executions Recorded: {summary["executions_recorded"]}
Active Timers: {summary["active_timers"]}
Alerts: {summary["alerts_count"]}
Failure Rate: {summary["failure_rate"]:.2%}
Components: {summary["components_monitored"]}"""
            summary_label.config(text=summary_text)

            exec_text.delete("1.0", "end")
            exec_text.insert(
                "end", json.dumps(self.get_recent_executions(50), indent=2)
            )

            alert_text.delete("1.0", "end")
            alert_text.insert("end", json.dumps(self.get_alerts(50), indent=2))

        ttk.Button(summary_tab, text="Refresh", command=refresh).pack(pady=5)
        ttk.Button(
            summary_tab,
            text="Generate Report",
            command=lambda: print(json.dumps(self.generate_report(), indent=2)),
        ).pack(pady=5)

        root.mainloop()


def main():
    """CLI entry point."""

    parser = argparse.ArgumentParser(description="METRICZ - Execution Telemetry")
    parser.add_argument("--version", action="version", version="1.0.0")
    parser.add_argument("--summary", action="store_true", help="Show summary")
    parser.add_argument("--executions", type=int, default=20, help="Recent executions")
    parser.add_argument("--alerts", action="store_true", help="Show alerts")
    parser.add_argument("--performance", action="store_true", help="Show performance")
    parser.add_argument("--report", type=int, default=24, help="Generate report")
    parser.add_argument("--record", nargs="+", help="Record metric: name value")
    parser.add_argument("--popup", action="store_true", help="Launch GUI")

    args = parser.parse_args()
    mz = METRICZ()

    if args.summary:
        print(json.dumps(mz.get_summary(), indent=2))
    elif args.alerts:
        print(json.dumps(mz.get_alerts(args.executions), indent=2))
    elif args.performance:
        print(json.dumps([asdict(p) for p in mz.get_performance_summary()], indent=2))
    elif args.report:
        print(json.dumps(mz.generate_report(args.report), indent=2))
    elif args.record:
        mz.record_metric(args.record[0], float(args.record[1]))
        print(f"Recorded: {args.record[0]} = {args.record[1]}")
    elif args.popup:
        mz.run_popup()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
