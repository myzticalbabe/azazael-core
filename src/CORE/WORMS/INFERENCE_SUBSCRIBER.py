# coding: utf-8
"""
INFERENCE_SUBSCRIBER.py — MemoryBus Inference Subscriber Worm

Subscribes to MemoryBus topics and uses LLM inference for real-time
anomaly detection across the Azazael ecosystem. Passively listens to
cluster events and flags patterns requiring attention.

Architecture:
    InferenceSubscriberWorm
    +-- Subscribes to MemoryBus topics: cluster.*, nature.*, inference.*
    +-- Buffers recent events for pattern analysis
    +-- Periodically analyzes event patterns via InferenceWorm
    +-- Publishes anomaly alerts to MemoryBus topic "inference.alert"

Usage:
    from CORE.WORMS.INFERENCE_SUBSCRIBER import InferenceSubscriberWorm
    worm = InferenceSubscriberWorm()
    worm.start()  # Begins listening to MemoryBus
    # ... cluster operations happen ...
    alerts = worm.get_alerts()  # View detected anomalies
    worm.stop()

Features:
- Passive listener — never blocks cluster operations
- Configurable analysis interval (default: 30s)
- Anomaly pattern detection: failure spikes, timeout cascades, routing gaps
- LLM-powered interpretation of event patterns
- Publishes alerts to MemoryBus for downstream consumers
- Thread-safe event buffer with configurable capacity

Version: 1.0.0
"""

import threading
from datetime import datetime
from typing import Any, List
from collections import deque

from CORE.worm_registry import register_worm

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__version__ = "1.0.0"


@register_worm(
    category="analysis",
    priority=5,
    cooldown_seconds=30.0,
    description="MemoryBus inference subscriber — real-time anomaly detection via LLM"
)
class InferenceSubscriberWorm:
    """Passive MemoryBus listener that detects anomalies via LLM analysis.

    Subscribes to cluster, nature, and inference events on the MemoryBus.
    Buffers events and periodically runs LLM analysis to detect patterns.
    Publishes alerts to MemoryBus topic "inference.alert".
    """

    VERSION = "1.0.0"

    def __init__(self, max_events: int = 200, analysis_interval_sec: float = 30.0):
        self._events: deque = deque(maxlen=max_events)
        self._alerts: list[dict[str, Any]] = []
        self._running = False
        self._lock = threading.Lock()
        self._analysis_interval = analysis_interval_sec
        self._bus = None
        self._inference_worm = None
        self._analysis_count = 0
        self._last_analysis: (str | None) = None

    # -- Lifecycle ---------------------------------------------------

    def start(self):
        """Start listening to MemoryBus events."""
        if self._running:
            return
        self._running = True

        # Subscribe to MemoryBus
        try:
            from CORE.MemoryBus import MemoryBus
            self._bus = MemoryBus()
            # Use prefix wildcard (>) to catch multi-segment topics
            self._bus.subscribe("cluster.>", self._on_cluster_event)
            self._bus.subscribe("category.>", self._on_category_event)
            self._bus.subscribe("inference.>", self._on_inference_event)
        except ImportError as exc:
            note_failure("INFERENCE_SUBSCRIBER:L88", exc)

    def stop(self):
        """Stop listening and clean up subscriptions."""
        self._running = False
        if self._bus:
            for pattern in ("cluster.>", "category.>", "inference.>"):
                try:
                    self._bus.unsubscribe(pattern)
                except Exception as exc:
                    note_failure("INFERENCE_SUBSCRIBER:L98", exc)

    # -- Event Handlers ----------------------------------------------

    def _on_cluster_event(self, message):
        """Handle cluster.* events from MemoryBus."""
        self._buffer_event("cluster", message)

    def _on_category_event(self, message):
        """Handle nature.* events from MemoryBus."""
        self._buffer_event("nature", message)

    def _on_inference_event(self, message):
        """Handle inference.* events from MemoryBus."""
        self._buffer_event("inference", message)

    def _buffer_event(self, category: str, message):
        """Buffer an event for later analysis."""
        with self._lock:
            self._events.append({
                "category": category,
                "topic": getattr(message, "topic", "unknown"),
                "content": getattr(message, "content", str(message)),
                "source": getattr(message, "source", "unknown"),
                "timestamp": datetime.now().isoformat(),
            })

    # -- Analysis ---------------------------------------------------

    def analyze(self) -> dict[str, Any]:
        """Run LLM-powered analysis on buffered events.

        Detects patterns: failure spikes, timeout cascades, routing gaps,
        and other anomalies that warrant attention.

        Returns:
            dict with 'alerts' (list), 'summary' (str), 'event_count' (int).
        """
        with self._lock:
            events_snapshot = list(self._events)
            event_count = len(events_snapshot)

        if event_count < 3:
            return {"alerts": [], "summary": "Insufficient events for analysis", "event_count": event_count}

        # Quick statistical checks (no LLM needed)
        quick_alerts = self._quick_checks(events_snapshot)

        # LLM-powered deep analysis
        llm_alerts = self._llm_analyze(events_snapshot)

        all_alerts = quick_alerts + llm_alerts

        with self._lock:
            self._alerts.extend(all_alerts)
            self._analysis_count += 1
            self._last_analysis = datetime.now().isoformat()

        # Publish alerts to MemoryBus
        for alert in all_alerts:
            self._publish_alert(alert)

        return {
            "alerts": all_alerts,
            "summary": f"Analyzed {event_count} events, found {len(all_alerts)} alerts",
            "event_count": event_count,
        }

    def _quick_checks(self, events: list[dict]) -> list[dict]:
        """Fast statistical checks that don't require LLM."""
        alerts = []

        # Check for failure spikes
        failure_events = [
            e for e in events
            if "fail" in str(e.get("content", "")).lower()
            or "error" in str(e.get("content", "")).lower()
        ]
        if len(failure_events) > len(events) * 0.3:
            alerts.append({
                "type": "failure_spike",
                "severity": "warning",
                "message": f"High failure rate: {len(failure_events)}/{len(events)} events indicate errors",
                "timestamp": datetime.now().isoformat(),
            })

        # Check for timeout patterns
        timeout_events = [
            e for e in events
            if "timeout" in str(e.get("content", "")).lower()
        ]
        if len(timeout_events) >= 3:
            alerts.append({
                "type": "timeout_cascade",
                "severity": "warning",
                "message": f"{len(timeout_events)} timeout events detected — possible cascade",
                "timestamp": datetime.now().isoformat(),
            })

        return alerts

    def _llm_analyze(self, events: list[dict]) -> list[dict]:
        """Use LLM to deeply analyze event patterns."""
        worm = self._get_inference()
        # _get_inference caches False when the optional import fails; the
        # isinstance check narrows away the checker's Literal[True] remnant
        # so only the usable worm survives (falsy values already returned).
        if not worm or isinstance(worm, bool):
            return []

        # Build event summary
        event_summary = []
        category_counts: dict[str, int] = {}
        for e in events:
            cat = e.get("category", "unknown")
            category_counts[cat] = category_counts.get(cat, 0) + 1
            content_preview = str(e.get("content", ""))[:800]
            event_summary.append(f"[{cat}] {content_preview}")

        prompt = (
            f"Azazael ecosystem events (last {len(events)}):\n"
            f"Categories: {category_counts}\n"
            f"Recent events:\n" +
            "\n".join(f"  {s}" for s in event_summary[-10:]) + "\n\n"
            "Identify any anomalies, patterns, or issues. "
            "Respond with up to 2 alerts (if any). Format: "
            "ALERT: <type> | <message> (one per line). "
            "If no issues, respond: OK"
        )

        try:
            result = worm.execute(
                prompt=prompt,
                system_prompt=(
                    "You monitor an AI ecosystem called Azazael. "
                    "Detect anomalies in event patterns. Be concise."
                ),
                max_tokens=150,
            )
            if result.get("success") and result.get("content"):
                content = result["content"]
                if "OK" in content.upper() and "ALERT" not in content.upper():
                    return []

                alerts = []
                for line in content.split("\n"):
                    line = line.strip()
                    if line.upper().startswith("ALERT:"):
                        parts = line.split(":", 1)[-1].strip().split("|", 1)
                        alert_type = parts[0].strip() if parts else "anomaly"
                        message = parts[1].strip() if len(parts) > 1 else line
                        alerts.append({
                            "type": alert_type,
                            "severity": "info",
                            "message": message,
                            "source": "llm_analysis",
                            "timestamp": datetime.now().isoformat(),
                        })
                return alerts
        except Exception as exc:
            note_failure("INFERENCE_SUBSCRIBER:L255", exc)

        return []

    # -- Helpers -----------------------------------------------------

    def _get_inference(self):
        """Lazy-load InferenceWorm."""
        if self._inference_worm is None:
            try:
                from CORE.WORMS.INFERENCE_WORM import InferenceWorm
                self._inference_worm = InferenceWorm()
            except ImportError as exc:
                note_failure("INFERENCE_SUBSCRIBER:L268", exc)
                self._inference_worm = False
        return self._inference_worm if self._inference_worm else None

    def _publish_alert(self, alert: dict[str, Any]):
        """Publish an alert to MemoryBus (best-effort)."""
        try:
            if self._bus:
                self._bus.publish(
                    "inference.alert",
                    alert,
                    source="InferenceSubscriberWorm",
                )
        except Exception as exc:
            note_failure("INFERENCE_SUBSCRIBER:L281", exc)

    # -- Public API --------------------------------------------------

    def get_alerts(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent alerts."""
        with self._lock:
            return list(self._alerts[-limit:])

    def get_stats(self) -> dict[str, Any]:
        """Get subscriber statistics."""
        with self._lock:
            return {
                "version": self.VERSION,
                "running": self._running,
                "events_buffered": len(self._events),
                "alerts_generated": len(self._alerts),
                "analysis_count": self._analysis_count,
                "last_analysis": self._last_analysis,
            }

    def execute(self, prompt: str = "", **kwargs) -> dict[str, Any]:
        """Worm execute() — runs analysis on buffered events."""
        return self.analyze()

    def __repr__(self) -> str:
        stats = self.get_stats()
        return (
            f"InferenceSubscriberWorm(v{self.VERSION}, "
            f"events={stats['events_buffered']}, "
            f"alerts={stats['alerts_generated']}, "
            f"running={stats['running']})"
        )


# --- Standalone test ------------------------------------------------

if __name__ == "__main__":
    print(f"InferenceSubscriberWorm v{InferenceSubscriberWorm.VERSION}")
    print("=" * 60)

    worm = InferenceSubscriberWorm()

    # Simulate events
    worm._buffer_event("cluster", type(
        "Msg", (), {"topic": "cluster.execute.start", "content": {"worm_count": 5}, "source": "ClusterWORMZ"}
    ))
    worm._buffer_event("nature", type(
        "Msg", (), {"topic": "nature.route.security.defense", "content": {"ok": 4, "fail": 1}, "source": "Router"}
    ))
    worm._buffer_event("cluster", type(
        "Msg", (), {"topic": "cluster.execute.error", "content": {"error": "timeout"}, "source": "ClusterWORMZ"}
    ))

    # Run analysis
    result = worm.analyze()
    print(f"Analysis: {result['summary']}")
    print(f"Alerts: {len(result['alerts'])}")
    for alert in result["alerts"]:
        print(f"  [{alert['type']}] {alert['message']}")

    print(f"\nStats: {worm.get_stats()}")
    print("Done.")
