"""
RULE 0X-8: Compliance Telemetry

Lightweight event emission system for tracking compliance actions.
Emits structured events to a ring buffer, with optional callbacks
for external integrations (logging, audit trail, etc.).

No OpenTelemetry dependency — uses stdlib for zero-dependency operation.
"""

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


# ---------------------------------------------------------------------------
# Telemetry Event
# ---------------------------------------------------------------------------

@dataclass
class TelemetryEvent:
    """A single compliance telemetry event.

    Attributes:
        event_type: Type identifier (e.g., "gate_assertion", "code_edit", "review")
        component: Which component emitted the event (e.g., "policy", "state_machine")
        status: "pass", "fail", "info", "error"
        details: Arbitrary structured data
        ts: ISO timestamp (auto-generated if empty)
    """
    event_type: str
    component: str
    status: str  # "pass", "fail", "info", "error"
    details: (dict[str, Any] | None) = None
    ts: str = ""

    def __post_init__(self):
        if not self.ts:
            self.ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "event_type": self.event_type,
            "component": self.component,
            "status": self.status,
            "details": self.details or {},
        }


# ---------------------------------------------------------------------------
# Compliance Telemetry
# ---------------------------------------------------------------------------

class ComplianceTelemetry:
    """Ring-buffer-based telemetry for compliance events.

    Stores events in an in-memory ring buffer. Provides callbacks for
    external integrations (e.g., writing to .audit/ JSONL files).

    Usage:
        tel = ComplianceTelemetry(max_events=1000)

        tel.emit("gate_passed", "policy", "pass",
                 {"action": "str_replace", "role": "developer"})

        # Register external callback
        def log_callback(event):
            print(f"[TELEMETRY] {event.event_type}: {event.status}")
        tel.register_callback(log_callback)
    """

    def __init__(self, max_events: int = 1000):
        self._max_events = max_events
        self._events: deque = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[TelemetryEvent], None]] = []
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value

    @property
    def event_count(self) -> int:
        with self._lock:
            return len(self._events)

    def emit(self, event_type: str, component: str, status: str,
             details: (dict[str, Any] | None) = None) -> TelemetryEvent:
        """Emit a telemetry event.

        Args:
            event_type: Type identifier
            component: Which component emitted the event
            status: "pass", "fail", "info", or "error"
            details: Optional structured data

        Returns:
            The created TelemetryEvent
        """
        if not self._enabled:
            return TelemetryEvent(event_type, component, status, details)

        event = TelemetryEvent(event_type, component, status, details)

        with self._lock:
            self._events.append(event)

        # Notify callbacks
        for callback in self._callbacks:
            try:
                callback(event)
            except Exception as exc:
                note_failure("telemetry:L120", exc)

        return event

    def emit_pass(self, event_type: str, component: str, details: (dict | None) = None) -> TelemetryEvent:
        """Convenience: emit a 'pass' status event."""
        return self.emit(event_type, component, "pass", details)

    def emit_fail(self, event_type: str, component: str, details: (dict | None) = None) -> TelemetryEvent:
        """Convenience: emit a 'fail' status event."""
        return self.emit(event_type, component, "fail", details)

    def emit_error(self, event_type: str, component: str, details: (dict | None) = None) -> TelemetryEvent:
        """Convenience: emit an 'error' status event."""
        return self.emit(event_type, component, "error", details)

    def emit_info(self, event_type: str, component: str, details: (dict | None) = None) -> TelemetryEvent:
        """Convenience: emit an 'info' status event."""
        return self.emit(event_type, component, "info", details)

    def register_callback(self, callback: Callable[[TelemetryEvent], None]):
        """Register a callback that receives every emitted event.

        Callbacks are called synchronously. Do not register blocking callbacks.
        """
        with self._lock:
            self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable):
        with self._lock:
            self._callbacks = [c for c in self._callbacks if c is not callback]

    def get_events(self, limit: int = 100, event_type: (str | None) = None,
                   status: (str | None) = None) -> list[dict[str, Any]]:
        """Get recent events, with optional filtering."""
        with self._lock:
            events = list(self._events)

        # Filter
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        if status:
            events = [e for e in events if e.status == status]

        # Return most recent first, limited
        events.reverse()
        return [e.to_dict() for e in events[:limit]]

    def get_summary(self) -> dict[str, Any]:
        """Get summary statistics."""
        with self._lock:
            events = list(self._events)

        type_counts: dict[str, int] = {}
        status_counts: dict[str, int] = {}
        component_counts: dict[str, int] = {}

        for e in events:
            type_counts[e.event_type] = type_counts.get(e.event_type, 0) + 1
            status_counts[e.status] = status_counts.get(e.status, 0) + 1
            component_counts[e.component] = component_counts.get(e.component, 0) + 1

        return {
            "total_events": len(events),
            "enabled": self._enabled,
            "by_type": type_counts,
            "by_status": status_counts,
            "by_component": component_counts,
        }

    def clear(self):
        """Clear all events."""
        with self._lock:
            self._events.clear()

    def __repr__(self) -> str:
        return f"ComplianceTelemetry(events={self.event_count}, enabled={self._enabled})"
