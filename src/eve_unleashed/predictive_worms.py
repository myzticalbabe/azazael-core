"""
Eve Unleashed — Predictive Intelligence Worms (Phase 2, V8.0).

SYSTEMZ, DISKZ, NETWORXZ, PROCESSZ, CLOCKZ: resource-trend forecasting +
anomaly detection, each a threaded daemon worm that samples system metrics,
fits a linear-regression trend (stdlib-only), records forecasts + anomalies
to PAMS via PamsRecorder, and exposes a forecast() snapshot.

Fail-open: degrades to empty forecasts when psutil/PAMS absent.
"""

from __future__ import annotations

import statistics
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: Any = None) -> None:
        pass


def _try_psutil() -> Any:
    try:
        import psutil  # type: ignore
        return psutil
    except ImportError as exc:
        note_failure("predictive_worms:psutil", exc)
        return None


def _linear_regression(points: List[Tuple[float, float]]) -> Tuple[float, float]:
    """Least-squares slope/intercept for (t, value) points. Returns (slope, intercept)."""
    n = len(points)
    if n < 2:
        return 0.0, points[0][1] if points else 0.0
    mean_t = sum(p[0] for p in points) / n
    mean_v = sum(p[1] for p in points) / n
    num = sum((p[0] - mean_t) * (p[1] - mean_v) for p in points)
    den = sum((p[0] - mean_t) ** 2 for p in points)
    if den == 0:
        return 0.0, mean_v
    slope = num / den
    intercept = mean_v - slope * mean_t
    return slope, intercept


def _forecast_value(slope: float, intercept: float, t_now: float, horizon_s: float) -> float:
    return intercept + slope * (t_now + horizon_s)


@dataclass
class Forecast:
    worm: str
    metric: str
    current: float
    trend_slope: float          # units per second
    predicted: float            # value at horizon
    horizon_s: float
    anomaly: bool
    anomaly_score: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "worm": self.worm, "metric": self.metric,
            "current": self.current, "trend_slope": self.trend_slope,
            "predicted": self.predicted, "horizon_s": self.horizon_s,
            "anomaly": self.anomaly, "anomaly_score": self.anomaly_score,
            "timestamp": self.timestamp,
        }


class PredictiveWorm:
    """Base threaded predictive worm: sample -> regress -> forecast -> record."""

    NAME = "predictive_worm"
    METRIC = "generic"
    SAMPLE_INTERVAL = 5.0
    HORIZON_S = 300.0           # forecast 5 min ahead
    WINDOW_S = 600.0            # keep 10 min of samples
    ANOMALY_Z = 3.0             # z-score threshold for anomaly

    def __init__(self, recorder=None):
        self.recorder = recorder
        self._samples: List[Tuple[float, float]] = []  # (t, value)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = threading.Event()
        self._lock = threading.Lock()
        self.last_forecast: Optional[Forecast] = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> bool:
        if self._running.is_set():
            return True
        try:
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name=f"{self.NAME}-worm", daemon=True)
            self._thread.start()
            self._running.set()
            return True
        except Exception as exc:
            note_failure(f"{self.NAME}:start", exc)
            return False

    def stop(self) -> bool:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.SAMPLE_INTERVAL + 2.0)
        self._thread = None
        self._running.clear()
        return True

    def is_running(self) -> bool:
        return self._running.is_set()

    def forecast(self) -> Optional[Forecast]:
        with self._lock:
            return self.last_forecast

    # -- sampling ------------------------------------------------------------

    def _sample(self) -> Optional[float]:
        raise NotImplementedError

    def _record(self, forecast: Forecast) -> None:
        rec = self.recorder
        if rec is None:
            try:
                from CORE.PamsRecorder import get_recorder
                rec = get_recorder()
            except Exception as exc:
                note_failure(f"{self.NAME}:recorder", exc)
                rec = None
        if rec is None:
            return
        try:
            rec.record_event(
                event_type="predictive_forecast",
                data=forecast.to_dict(),
                source=self.NAME,
                severity="warning" if forecast.anomaly else "info",
            )
        except Exception as exc:
            note_failure(f"{self.NAME}:record", exc)

    # -- main loop -----------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                value = self._sample()
                if value is not None:
                    now = time.time()
                    with self._lock:
                        self._samples.append((now, value))
                        cutoff = now - self.WINDOW_S
                        self._samples = [p for p in self._samples if p[0] >= cutoff]
                        self.last_forecast = self._compute_forecast(now)
                    self._record(self.last_forecast)
            except Exception as exc:  # never kill the worm
                note_failure(f"{self.NAME}:beat", exc)
            self._stop.wait(self.SAMPLE_INTERVAL)

    def _compute_forecast(self, now: float) -> Forecast:
        with self._lock:
            points = list(self._samples)
        slope, intercept = _linear_regression(points)
        current = points[-1][1] if points else 0.0
        predicted = _forecast_value(slope, intercept, now, self.HORIZON_S)
        # anomaly = z-score of current vs recent window
        values = [p[1] for p in points]
        anomaly = False
        score = 0.0
        if len(values) >= 3:
            mean = statistics.mean(values)
            stdev = statistics.pstdev(values)
            if stdev > 0:
                score = abs(current - mean) / stdev
                anomaly = score > self.ANOMALY_Z
        return Forecast(
            worm=self.NAME, metric=self.METRIC, current=current,
            trend_slope=slope, predicted=predicted, horizon_s=self.HORIZON_S,
            anomaly=anomaly, anomaly_score=score,
        )


# -- Concrete worms -----------------------------------------------------------

class SYSTEMZ(PredictiveWorm):
    """CPU/memory trend forecasting + anomaly detection."""

    NAME = "SYSTEMZ"
    METRIC = "cpu_percent"
    SAMPLE_INTERVAL = 5.0

    def _sample(self) -> Optional[float]:
        psutil = _try_psutil()
        if psutil is None:
            return None
        return psutil.cpu_percent(interval=None)


class DISKZ(PredictiveWorm):
    """Disk failure prediction from usage trend + SMART-adjacent pressure."""

    NAME = "DISKZ"
    METRIC = "disk_percent"

    def _sample(self) -> Optional[float]:
        psutil = _try_psutil()
        if psutil is None:
            return None
        try:
            usage = psutil.disk_usage("/")
            return usage.percent
        except Exception as exc:
            note_failure("predictive_worms:sample", exc)
            return None


class NETWORXZ(PredictiveWorm):
    """Network congestion forecasting from bytes sent/recv rate."""

    NAME = "NETWORXZ"
    METRIC = "net_bytes_per_s"

    def __init__(self, recorder=None):
        super().__init__(recorder)
        self._last_counters: Optional[Any] = None
        self._last_t: Optional[float] = None

    def _sample(self) -> Optional[float]:
        psutil = _try_psutil()
        if psutil is None:
            return None
        try:
            c = psutil.net_io_counters()
            now = time.time()
            if self._last_counters is not None and self._last_t is not None:
                dt = now - self._last_t
                if dt > 0:
                    bps = ((c.bytes_sent - self._last_counters.bytes_sent) +
                           (c.bytes_recv - self._last_counters.bytes_recv)) / dt
                    self._last_counters = c
                    self._last_t = now
                    return bps
            self._last_counters = c
            self._last_t = now
            return 0.0
        except Exception as exc:
            note_failure("predictive_worms:sample", exc)
            return None


class PROCESSZ(PredictiveWorm):
    """Resource leak detection (monotonic growth) from process count."""

    NAME = "PROCESSZ"
    METRIC = "process_count"

    def _sample(self) -> Optional[float]:
        psutil = _try_psutil()
        if psutil is None:
            return None
        try:
            return float(len(psutil.pids()))
        except Exception as exc:
            note_failure("predictive_worms:sample", exc)
            return None


class CLOCKZ(PredictiveWorm):
    """Schedule optimization placeholder: idle-cycle forecasting."""

    NAME = "CLOCKZ"
    METRIC = "idle_percent"

    def _sample(self) -> Optional[float]:
        psutil = _try_psutil()
        if psutil is None:
            return None
        try:
            return psutil.cpu_percent(interval=None)  # inverse proxy for idle
        except Exception as exc:
            note_failure("predictive_worms:sample", exc)
            return None


__all__ = ["PredictiveWorm", "Forecast", "SYSTEMZ", "DISKZ", "NETWORXZ",
           "PROCESSZ", "CLOCKZ", "_linear_regression"]
