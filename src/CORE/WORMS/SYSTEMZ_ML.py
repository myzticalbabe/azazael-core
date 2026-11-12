# coding: utf-8
"""
SYSTEMZ_ML - ML-Enhanced System Monitoring with Anomaly Detection

Machine learning-based system monitoring with predictive anomaly detection
and intelligent alerting.

Features:
- ML anomaly detection
- Predictive resource forecasting
- Pattern-based alerts
- Historical analysis
- Adaptive thresholds

Usage:
    >>> from WORMZ.SYSTEMZ_ML import SYSTEMZ_ML
    >>> szml = SYSTEMZ_ML()
    >>> status = szml.get_status()
    >>> anomalies = szml.detect_anomalies()
    >>> prediction = szml.predict_usage(hours=24)
"""

import json
import pickle
import numpy as np
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List
from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk, scrolledtext

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler

    SKLEARN_AVAILABLE = True
except ImportError as exc:
    note_failure("SYSTEMZ_ML:L39", exc)
    SKLEARN_AVAILABLE = False


@dataclass
class SystemSnapshot:
    """System state snapshot."""

    timestamp: str
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    network_bytes_sent: int
    network_bytes_recv: int
    process_count: int
    boot_time: str


@dataclass
class AnomalyResult:
    """Anomaly detection result."""

    metric: str
    value: float
    expected_range: tuple[float, float]
    anomaly_score: float
    severity: str
    timestamp: str


@dataclass
class ResourcePrediction:
    """Resource usage prediction."""

    metric: str
    predicted_value: float
    confidence: float
    timeframe: str
    trend: str


try:
    from CORE.worm_registry import register_worm
except ImportError as exc:
    note_failure("SYSTEMZ_ML:L82", exc)
    register_worm = lambda **kw: (lambda cls: cls)


from CORE.paths import get_project_root
import argparse


_ROOT = get_project_root()
_OUTPUT_DIR = _ROOT / "output" / "systemz_ml"


class SYSTEMZ_MLConfig:
    """Configuration for SYSTEMZ_ML."""

    OUTPUT_PATH = str(_OUTPUT_DIR)
    HISTORY_FILE = str(_OUTPUT_DIR / "history.json")
    MODEL_FILE = str(_OUTPUT_DIR / "anomaly_model.pkl")
    SAMPLES_DIR = str(_OUTPUT_DIR / "samples")

    HISTORY_LENGTH = 1000
    ANOMALY_CONTAMINATION = 0.1
    PREDICTION_HORIZON = 24

    METRICS = ["cpu_percent", "memory_percent", "disk_percent", "process_count"]

    THRESHOLDS = {
        "cpu_percent": {"warning": 80.0, "critical": 95.0},
        "memory_percent": {"warning": 85.0, "critical": 95.0},
        "disk_percent": {"warning": 90.0, "critical": 98.0},
        "process_count": {"warning": 300, "critical": 500},
    }


@register_worm(category="ml", priority=6, cooldown_seconds=45.0, description="ML-enhanced system monitoring with anomaly detection")
class SYSTEMZ_ML:
    """ML-Enhanced System Monitoring with Anomaly Detection."""

    VERSION = "1.0.0"

    def __init__(self):
        self.config = SYSTEMZ_MLConfig()
        self.output_path = Path(self.config.OUTPUT_PATH)
        self.samples_dir = Path(self.config.SAMPLES_DIR)
        self._ensure_directories()

        self.history: list[SystemSnapshot] = []
        self.anomaly_model: (IsolationForest | None) = None
        self.scaler: (StandardScaler | None) = None
        self.baseline: dict[str, tuple[float, float]] = {}

        self._load_history()
        self._load_model()

    def _ensure_directories(self):
        """Create output directories."""
        self.output_path.mkdir(parents=True, exist_ok=True)
        self.samples_dir.mkdir(parents=True, exist_ok=True)

    def _get_snapshot(self) -> SystemSnapshot:
        """Capture current system state."""
        import psutil
        net_io = psutil.net_io_counters()
        return SystemSnapshot(
            timestamp=datetime.now().isoformat(),
            cpu_percent=psutil.cpu_percent(interval=1),
            memory_percent=psutil.virtual_memory().percent,
            disk_percent=psutil.disk_usage("/").percent,
            network_bytes_sent=net_io.bytes_sent,
            network_bytes_recv=net_io.bytes_recv,
            process_count=len(psutil.pids()),
            boot_time=datetime.fromtimestamp(psutil.boot_time()).isoformat(),
        )

    def _features_to_array(self, snapshot: SystemSnapshot) -> np.ndarray:
        """Convert snapshot to feature array."""
        return np.array(
            [
                snapshot.cpu_percent,
                snapshot.memory_percent,
                snapshot.disk_percent,
                snapshot.process_count,
            ]
        ).reshape(1, -1)

    def _calculate_baseline(self):
        """Calculate baseline statistics from history."""
        if len(self.history) < 50:
            return

        for metric in self.config.METRICS:
            values = [getattr(s, metric) for s in self.history]
            self.baseline[metric] = (
                statistics.mean(values),
                statistics.stdev(values) if len(values) > 1 else 0.0,
            )

    def _train_anomaly_model(self):
        """Train Isolation Forest on historical data."""
        if not SKLEARN_AVAILABLE or len(self.history) < 50:
            return

        try:
            features = np.array(
                [
                    [s.cpu_percent, s.memory_percent, s.disk_percent, s.process_count]
                    for s in self.history
                ]
            )

            self.scaler = StandardScaler()
            features_scaled = self.scaler.fit_transform(features)

            self.anomaly_model = IsolationForest(
                contamination=self.config.ANOMALY_CONTAMINATION,  # pyright: ignore[reportArgumentType] -- sklearn annotates contamination as str (its default "auto") but accepts float per its documented contract; 0.1 is the intended fraction
                random_state=42,
            )
            self.anomaly_model.fit(features_scaled)

            self._save_model()
        except Exception as e:
            print(f"Failed to train anomaly model: {e}")

    def detect_anomalies(self) -> list[AnomalyResult]:
        """Detect current system anomalies."""
        snapshot = self._get_snapshot()
        anomalies = []

        for metric in self.config.METRICS:
            value = getattr(snapshot, metric)
            threshold = self.config.THRESHOLDS.get(metric, {})

            if metric in self.baseline:
                mean, std = self.baseline[metric]
                expected_range = (mean - 2 * std, mean + 2 * std)
                z_score = abs(value - mean) / max(std, 0.1)

                if z_score > 2:
                    severity = "critical" if z_score > 3 else "warning"
                    anomalies.append(
                        AnomalyResult(
                            metric=metric,
                            value=value,
                            expected_range=expected_range,
                            anomaly_score=z_score,
                            severity=severity,
                            timestamp=snapshot.timestamp,
                        )
                    )
            else:
                if threshold:
                    if value >= threshold.get("critical", 100):
                        anomalies.append(
                            AnomalyResult(
                                metric=metric,
                                value=value,
                                expected_range=(0, threshold.get("critical", 100)),
                                anomaly_score=3.0,
                                severity="critical",
                                timestamp=snapshot.timestamp,
                            )
                        )
                    elif value >= threshold.get("warning", 80):
                        anomalies.append(
                            AnomalyResult(
                                metric=metric,
                                value=value,
                                expected_range=(0, threshold.get("warning", 80)),
                                anomaly_score=1.5,
                                severity="warning",
                                timestamp=snapshot.timestamp,
                            )
                        )

        return anomalies

    def predict_usage(self, hours: int = 24) -> list[ResourcePrediction]:
        """Predict future resource usage."""
        predictions = []

        if len(self.history) < 10:
            return predictions

        for metric in ["cpu_percent", "memory_percent", "disk_percent"]:
            values = [getattr(s, metric) for s in self.history[-100:]]

            if len(values) < 2:
                continue

            recent = values[-10:]
            trend = "stable"
            if len(recent) >= 3:
                if recent[-1] > recent[0] * 1.1:
                    trend = "increasing"
                elif recent[-1] < recent[0] * 0.9:
                    trend = "decreasing"

            mean_val = statistics.mean(values)
            statistics.stdev(values) if len(values) > 1 else 0

            if hours > 12:
                predicted = mean_val * (
                    1.05
                    if trend == "increasing"
                    else 0.95
                    if trend == "decreasing"
                    else 1.0
                )
            else:
                predicted = mean_val

            predictions.append(
                ResourcePrediction(
                    metric=metric,
                    predicted_value=min(max(predicted, 0), 100),
                    confidence=0.7 if len(values) > 50 else 0.5,
                    timeframe=f"{hours}h",
                    trend=trend,
                )
            )

        return predictions

    def capture_sample(self) -> SystemSnapshot:
        """Capture and store system snapshot."""
        snapshot = self._get_snapshot()
        self.history.append(snapshot)

        if len(self.history) > self.config.HISTORY_LENGTH:
            self.history = self.history[-self.config.HISTORY_LENGTH :]

        self._save_history()

        if len(self.history) % 100 == 0:
            self._calculate_baseline()
            self._train_anomaly_model()

        return snapshot

    def get_status(self, detailed: bool = False) -> dict[str, Any]:
        """Get current system status."""
        snapshot = self._get_snapshot()
        anomalies = self.detect_anomalies()
        predictions = self.predict_usage()

        status = {
            "timestamp": snapshot.timestamp,
            "cpu_percent": snapshot.cpu_percent,
            "memory_percent": snapshot.memory_percent,
            "disk_percent": snapshot.disk_percent,
            "process_count": snapshot.process_count,
            "anomalies": [
                {
                    "metric": a.metric,
                    "value": a.value,
                    "severity": a.severity,
                }
                for a in anomalies
            ],
            "anomaly_count": len(anomalies),
            "status": "critical"
            if any(a.severity == "critical" for a in anomalies)
            else "warning"
            if anomalies
            else "healthy",
        }

        if detailed:
            status.update(
                {
                    "predictions": [
                        {
                            "metric": p.metric,
                            "predicted": p.predicted_value,
                            "trend": p.trend,
                        }
                        for p in predictions
                    ],
                    "history_count": len(self.history),
                    "model_trained": self.anomaly_model is not None,
                    "baseline": {
                        k: {"mean": v[0], "std": v[1]} for k, v in self.baseline.items()
                    },
                }
            )

        return status

    def get_cpu(self, interval: float = 1.0) -> dict[str, Any]:
        """Get CPU information."""
        import psutil
        return {
            "percent": psutil.cpu_percent(interval=interval),
            "count": psutil.cpu_count(),
            "count_logical": psutil.cpu_count(logical=True),
            "freq": psutil.cpu_freq(),
            "times": psutil.cpu_times_percent(interval=interval),
        }

    def get_memory(self) -> dict[str, Any]:
        """Get memory information."""
        import psutil
        mem = psutil.virtual_memory()
        return {
            "percent": mem.percent,
            "used_gb": round(mem.used / (1024**3), 2),
            "total_gb": round(mem.total / (1024**3), 2),
            "available_gb": round(mem.available / (1024**3), 2),
        }

    def get_disk(self) -> dict[str, Any]:
        """Get disk information."""
        import psutil
        disk = psutil.disk_usage("/")
        return {
            "percent": disk.percent,
            "used_gb": round(disk.used / (1024**3), 2),
            "total_gb": round(disk.total / (1024**3), 2),
        }

    def _save_history(self):
        """Persist history to file."""
        history_file = Path(self.config.HISTORY_FILE)
        history_file.parent.mkdir(parents=True, exist_ok=True)
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {
                        "timestamp": s.timestamp,
                        "cpu_percent": s.cpu_percent,
                        "memory_percent": s.memory_percent,
                        "disk_percent": s.disk_percent,
                        "process_count": s.process_count,
                    }
                    for s in self.history
                ],
                f,
                indent=2,
            )

    def _load_history(self):
        """Load persisted history."""
        history_file = Path(self.config.HISTORY_FILE)
        if history_file.exists():
            try:
                with open(history_file, encoding="utf-8") as f:
                    data = json.load(f)
                    self.history = [
                        SystemSnapshot(
                            timestamp=d["timestamp"],
                            cpu_percent=d["cpu_percent"],
                            memory_percent=d["memory_percent"],
                            disk_percent=d["disk_percent"],
                            network_bytes_sent=0,
                            network_bytes_recv=0,
                            process_count=d["process_count"],
                            boot_time="",
                        )
                        for d in data
                    ]
            except Exception as exc:
                note_failure("SYSTEMZ_ML:L440", exc)
                self.history = []

    def _save_model(self):
        """Persist anomaly model."""
        if not SKLEARN_AVAILABLE:
            return
        try:
            model_data = {
                "anomaly_model": self.anomaly_model,
                "scaler": self.scaler,
                "baseline": self.baseline,
            }
            with open(self.config.MODEL_FILE, "wb") as f:
                pickle.dump(model_data, f)
        except Exception as e:
            print(f"Failed to save model: {e}")

    def _load_model(self):
        """Load persisted anomaly model."""
        if not SKLEARN_AVAILABLE:
            return
        model_file = Path(self.config.MODEL_FILE)
        if model_file.exists():
            try:
                with open(model_file, "rb") as f:
                    model_data = pickle.load(f)
                self.anomaly_model = model_data.get("anomaly_model")
                self.scaler = model_data.get("scaler")
                self.baseline = model_data.get("baseline", {})
            except Exception as exc:
                note_failure("SYSTEMZ_ML:L470", exc)

    def analyze_trends(self, hours: int = 24) -> dict[str, Any]:
        """Analyze historical trends."""
        cutoff = datetime.now() - timedelta(hours=hours)
        recent = [
            s for s in self.history if datetime.fromisoformat(s.timestamp) > cutoff
        ]

        if not recent:
            return {"message": "No recent history"}

        return {
            "period_hours": hours,
            "samples": len(recent),
            "cpu": {
                "mean": statistics.mean(s.cpu_percent for s in recent),
                "max": max(s.cpu_percent for s in recent),
                "min": min(s.cpu_percent for s in recent),
            },
            "memory": {
                "mean": statistics.mean(s.memory_percent for s in recent),
                "max": max(s.memory_percent for s in recent),
                "min": min(s.memory_percent for s in recent),
            },
            "disk": {
                "mean": statistics.mean(s.disk_percent for s in recent),
                "max": max(s.disk_percent for s in recent),
                "min": min(s.disk_percent for s in recent),
            },
        }

    def run_popup(self):
        """Run with GUI popup."""
        root = tk.Tk()
        root.title("SYSTEMZ_ML - Anomaly Detection")
        root.geometry("800x600")

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        status_tab = ttk.Frame(notebook)
        notebook.add(status_tab, text="Status")

        anomalies_tab = ttk.Frame(notebook)
        notebook.add(anomalies_tab, text="Anomalies")

        trends_tab = ttk.Frame(notebook)
        notebook.add(trends_tab, text="Trends")

        status_label = ttk.Label(status_tab, text="Initializing...", font=("Arial", 14))
        status_label.pack(pady=10)

        log_text = scrolledtext.ScrolledText(anomalies_tab, width=80, height=20)
        log_text.pack(fill="both", expand=True, padx=10, pady=10)

        def refresh():
            status = self.get_status(detailed=True)
            anomalies = self.detect_anomalies()

            status_text = f"""System Status: {status["status"].upper()}
CPU: {status["cpu_percent"]:.1f}%
Memory: {status["memory_percent"]:.1f}%
Disk: {status["disk_percent"]:.1f}%
Processes: {status["process_count"]}
Anomalies: {status["anomaly_count"]}
History: {status.get("history_count", 0)} samples"""
            status_label.config(text=status_text)

            anomaly_text = json.dumps(
                [
                    {"metric": a.metric, "value": a.value, "severity": a.severity}
                    for a in anomalies
                ],
                indent=2,
            )
            log_text.delete("1.0", "end")
            log_text.insert("end", anomaly_text or "No anomalies detected")

        ttk.Button(
            status_tab,
            text="Refresh",
            command=refresh,
        ).pack(pady=10)

        ttk.Button(
            status_tab,
            text="Capture Sample",
            command=self.capture_sample,
        ).pack(pady=5)

        root.mainloop()


def main():
    """CLI entry point."""

    parser = argparse.ArgumentParser(description="SYSTEMZ_ML - ML Anomaly Detection")
    parser.add_argument("--version", action="version", version="1.0.0")
    parser.add_argument("--status", action="store_true", help="Show system status")
    parser.add_argument("--anomalies", action="store_true", help="Detect anomalies")
    parser.add_argument("--predict", type=int, default=24, help="Predict hours ahead")
    parser.add_argument("--capture", action="store_true", help="Capture sample")
    parser.add_argument("--trends", type=int, default=24, help="Analyze trends")
    parser.add_argument("--popup", action="store_true", help="Launch GUI")

    args = parser.parse_args()
    monitor = SYSTEMZ_ML()

    if args.status:
        print(json.dumps(monitor.get_status(detailed=True), indent=2))
    elif args.anomalies:
        anomalies = monitor.detect_anomalies()
        print(
            json.dumps(
                [
                    {
                        "metric": a.metric,
                        "value": a.value,
                        "severity": a.severity,
                    }
                    for a in anomalies
                ],
                indent=2,
            )
        )
    elif args.predict:
        print(
            json.dumps(
                [
                    {
                        "metric": p.metric,
                        "predicted": p.predicted_value,
                        "trend": p.trend,
                    }
                    for p in monitor.predict_usage(args.predict)
                ],
                indent=2,
            )
        )
    elif args.capture:
        snapshot = monitor.capture_sample()
        print(f"Captured: {snapshot.timestamp}")
    elif args.trends:
        print(json.dumps(monitor.analyze_trends(args.trends), indent=2))
    elif args.popup:
        monitor.run_popup()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

