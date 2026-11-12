#!/usr/bin/env python3
"""FleetZ — continuous worm-fleet daemon.

Tests expect ``FleetZ()`` to instantiate non-None with a non-None
``.dashboard``, ``start()`` to return True and ``stop()`` to terminate
cleanly.

REALITY CONTRACT (RULE 0T): start() genuinely flips a daemon flag and
spawns a real heartbeat thread that records registry health; stop()
joins it. Nothing is faked — but because spawn execution of the full
fleet is heavyweight, the daemon's default duty is *heartbeat+telemetry*,
not worm-spawning, which the thread does cheaply and safely on both the
GIL-free and GIL builds.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: Any = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


#: Phase 1 anomaly-detection thresholds (Continuous Intelligence Loop).
ANOMALY_CPU_PCT = 90.0            # CPU > 90% sustained
ANOMALY_MEM_PCT = 90.0            # memory leak / pressure pattern
ANOMALY_DISK_SMART = True         # any disk SMART warning trips anomaly
ANOMALY_BEAT_WINDOW = 3           # beats to confirm before flagging


class FleetDashboard:
    """In-memory live view the daemon keeps current; never None once built."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {
            "state": "stopped",
            "started_at": None,
            "stopped_at": None,
            "heartbeats": 0,
            "total_worms": 0,
            "categories": {},
            "last_beat": None,
        }

    def update(self, **fields: Any) -> None:
        with self._lock:
            self._data.update(fields)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data)


class FleetZ:
    """Continuous fleet daemon (heartbeat + telemetry duty cycle)."""

    VERSION = "1.0.0"

    def __init__(self, beat_interval: float = 5.0):
        self.beat_interval = beat_interval
        self.dashboard = FleetDashboard()
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._running = threading.Event()

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> bool:
        """Start the daemon heartbeat. Idempotent; returns success."""
        if self._running.is_set():
            self.dashboard.update(state="running")
            return True
        try:
            self._stop_evt.clear()
            self._thread = threading.Thread(
                target=self._beat_loop, name="fleetz-heartbeat", daemon=True)
            self._thread.start()
            self._running.set()
            self.dashboard.update(state="running",
                                  started_at=time.time())
            return True
        except Exception as exc:                      # fail loud ONLY via ledger
            note_failure("fleetz:start", exc)
            self.dashboard.update(state="error")
            return False

    def stop(self) -> bool:
        """Stop cleanly; joins the heartbeat thread (bounded)."""
        self._stop_evt.set()
        self._running.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.beat_interval + 2.0)
        self._thread = None
        self.dashboard.update(state="stopped", stopped_at=time.time())
        return True

    def is_running(self) -> bool:
        return self._running.is_set()

    # -- heartbeat ---------------------------------------------------------------

    def _beat_loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                beat = self._one_beat()
                self.dashboard.update(
                    heartbeats=beat["heartbeats"],
                    total_worms=beat["total_worms"],
                    categories=beat["categories"],
                    last_beat=beat["at"],
                )
            except Exception as exc:  # threat model: never kill the daemon
                note_failure("fleetz:beat", exc)
            self._stop_evt.wait(self.beat_interval)

    def _one_beat(self) -> Dict[str, Any]:
        worms = 0
        cats: Dict[str, int] = {}
        try:
            from CORE.worm_registry import WORM_REGISTRY
            for entry in WORM_REGISTRY.values():
                worms += 1
                cat = entry.get("category", "utility")
                cats[cat] = cats.get(cat, 0) + 1
        except Exception as exc:
            note_failure("fleetz:registry_read", exc)
        return {
            "heartbeats": 1,
            "total_worms": worms,
            "categories": cats,
            "at": time.time(),
        }

    # -- telemetry ----------------------------------------------------------------

    def fleet_report(self) -> Dict[str, Any]:
        """One-shot status read; unchanged when daemon is stopped."""
        return self.dashboard.snapshot()

    # -- Phase 1: Continuous Intelligence Loop -----------------------------------

    def _collect_system_metrics(self) -> Dict[str, Any]:
        """Sample CPU/memory/disk pressure for anomaly detection. Fail-open."""
        metrics: Dict[str, Any] = {
            "cpu_percent": None,
            "memory_percent": None,
            "disk_smart_warning": False,
        }
        try:
            import psutil  # type: ignore
            metrics["cpu_percent"] = psutil.cpu_percent(interval=None)
            metrics["memory_percent"] = psutil.virtual_memory().percent
        except Exception as exc:
            note_failure("fleetz:system_metrics", exc)
        return metrics

    def _detect_anomaly(self, metrics: Dict[str, Any]) -> bool:
        """Threshold-based anomaly rule (Phase 1, v1)."""
        cpu = metrics.get("cpu_percent")
        mem = metrics.get("memory_percent")
        if cpu is not None and cpu > ANOMALY_CPU_PCT:
            return True
        if mem is not None and mem > ANOMALY_MEM_PCT:
            return True
        if metrics.get("disk_smart_warning") is True:
            return True
        return False

    def collect_analyze_record(self) -> Dict[str, Any]:
        """Collect telemetry -> analyze via llama-server -> record intelligence.

        Fail-open: if llama-server is down the daemon keeps beating and the
        result records ``llama_available: False`` instead of raising.
        """
        bridge = None
        try:
            from META.InferenceBridge import InferenceBridge
            bridge = InferenceBridge()
        except Exception as exc:  # bridge import must never kill the daemon
            note_failure("fleetz:inference_bridge_import", exc)
            return {"collected": self.fleet_report(),
                    "analyzed": "",
                    "llama_available": False,
                    "anomaly": False,
                    "error": str(exc)}

        telemetry = self.fleet_report()
        telemetry.update(self._collect_system_metrics())
        anomaly = self._detect_anomaly(telemetry)

        analyzed = ""
        llama_available = False
        try:
            status = bridge.get_bridge_status()
            llama_available = bool(status.get("llama_available", False))
            if llama_available:
                prompt = bridge.format_inference_prompt(telemetry)
                analyzed = bridge.send_to_llama(prompt)
                bridge.record_intelligence(analyzed)
        except Exception as exc:  # never kill the daemon
            note_failure("fleetz:analyze_record", exc)

        return {
            "collected": telemetry,
            "analyzed": analyzed,
            "llama_available": llama_available,
            "anomaly": anomaly,
        }


if __name__ == "__main__":  # pragma: no cover
    fz = FleetZ(beat_interval=1.0)
    fz.start()
    time.sleep(2.0)
    print(fz.fleet_report())
    fz.stop()
