# coding: utf-8
"""
ErrorRecoveryOrchestrator — Centralized error recovery for all 38 worms.

Provides:
- Retry with exponential backoff
- Fallback to degraded mode
- Circuit breaking for consistently-failing worms
- Recovery pattern recording for MetaCognitionWorm

Part of AZAZAEL Worm Registry — Category: orchestration, Priority: 9
Module-level imports only — thread-safe by design (Lesson #29).
"""

import threading
import time
import random
from datetime import datetime
from typing import Any, Callable, List
from dataclasses import dataclass, field
from collections import defaultdict

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("error_recovery:L26", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls

# Module-level memory store import (3.14t safe — Lesson #29)
try:
    from CORE.local_memory import get_memory_store as _get_memory_store_global
    _LOCAL_MEMORY_OK = True
except ImportError as exc:
    note_failure("error_recovery:L35", exc)
    _LOCAL_MEMORY_OK = False
    _get_memory_store_global = None


@dataclass
class RecoveryEntry:
    """A single recovery attempt record."""
    worm_type: str
    attempt: int
    outcome: str  # "success", "failed", "circuit_open"
    duration_ms: float
    error: (str | None) = None
    strategy: str = "retry"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@register_worm(
    category="orchestration", priority=9, cooldown_seconds=30.0,
    description="Error recovery orchestrator — retry with exponential backoff, fallback, circuit-breaking for all 38 worms"
)
class ErrorRecoveryOrchestrator:
    """Centralized error recovery for worm execution.

    Wraps worm.execute() calls with:
    1. Exponential backoff retry (1s -> 2s -> 4s -> 8s -> ...)
    2. Fallback strategies when all retries exhausted
    3. Circuit breaker for worms failing faster than recovery
    4. Recovery pattern recording for MetaCognitionWorm learning

    Usage:
        ero = ErrorRecoveryOrchestrator()
        result = ero.execute_with_recovery("SystemZ", worm_instance)
        status = ero.get_circuit_breaker_status()
    """

    VERSION = "1.0.0"

    def __init__(self, max_retries: int = 3, base_backoff_ms: float = 1000.0,
                 circuit_threshold: int = 5, circuit_reset_seconds: float = 300.0):
        self._lock = threading.RLock()
        self.max_retries = max_retries
        self.base_backoff_ms = base_backoff_ms
        self.circuit_threshold = circuit_threshold
        self.circuit_reset_seconds = circuit_reset_seconds

        self._failure_counts: dict[str, int] = defaultdict(int)
        self._failure_windows: dict[str, list[float]] = defaultdict(list)  # monotonic timestamps
        self._circuit_open: dict[str, float] = {}  # worm_type -> monotonic opened_at
        self._recovery_history: list[RecoveryEntry] = []
        self._total_attempts = 0
        self._total_successes = 0
        self._last_cleanup: float = time.monotonic()  # Periodic cleanup tracking

    def execute_with_recovery(self, worm_type: str, worm_instance: Any,
                              execute_fn: (Callable | None) = None) -> dict[str, Any]:
        """Execute a worm with full error recovery pipeline.

        Args:
            worm_type: Worm type identifier
            worm_instance: Worm object with execute() method
            execute_fn: Optional custom execution function (default: worm_instance.execute)

        Returns:
            dict with result, recovery_stats, and strategy used
        """
        if execute_fn is None:
            execute_fn = lambda: worm_instance.execute()

        # Periodic cleanup of stale data across ALL worms (memory leak prevention)
        self._periodic_cleanup()

        # Check circuit breaker
        if self._is_circuit_open(worm_type):
            entry = RecoveryEntry(
                worm_type=worm_type, attempt=0, outcome="circuit_open",
                duration_ms=0, strategy="skip",
                error=f"Circuit breaker open for {worm_type}",
            )
            self._record_recovery(entry)
            return {
                "result": None,
                "outcome": "circuit_open",
                "recovery": {"strategy": "skip", "attempts": 0},
            }

        # Remove stale failure windows
        self._cleanup_failure_windows(worm_type)

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            start = time.perf_counter()
            try:
                result = execute_fn()
                duration_ms = (time.perf_counter() - start) * 1000

                entry = RecoveryEntry(
                    worm_type=worm_type, attempt=attempt, outcome="success",
                    duration_ms=round(duration_ms, 2), strategy="retry",
                )
                self._record_recovery(entry)

                # Reset failure count on success
                with self._lock:
                    self._failure_counts[worm_type] = 0

                return {
                    "result": result,
                    "outcome": "success",
                    "recovery": {
                        "strategy": "retry",
                        "attempts": attempt,
                        "duration_ms": round(duration_ms, 2),
                    },
                }

            except Exception as e:
                note_failure("error_recovery:L151", e)
                duration_ms = (time.perf_counter() - start) * 1000
                last_error = str(e)

                entry = RecoveryEntry(
                    worm_type=worm_type, attempt=attempt, outcome="failed",
                    duration_ms=round(duration_ms, 2), strategy="retry",
                    error=last_error,
                )
                self._record_recovery(entry)

                # Track failure
                with self._lock:
                    self._failure_counts[worm_type] += 1
                    self._failure_windows[worm_type].append(time.monotonic())
                    # Cap failure windows to prevent memory leak
                    if len(self._failure_windows[worm_type]) > 200:
                        self._failure_windows[worm_type] = self._failure_windows[worm_type][-200:]

                # Check circuit breaker threshold
                recent_fails = self._count_recent_failures(worm_type)
                if recent_fails >= self.circuit_threshold:
                    with self._lock:
                        self._circuit_open[worm_type] = time.monotonic()
                    entry = RecoveryEntry(
                        worm_type=worm_type, attempt=attempt, outcome="circuit_open",
                        duration_ms=round(duration_ms, 2), strategy="circuit_break",
                        error=f"Circuit threshold reached ({recent_fails} failures)",
                    )
                    self._record_recovery(entry)
                    return {
                        "result": None,
                        "outcome": "circuit_open",
                        "recovery": {"strategy": "circuit_break", "attempts": attempt},
                    }

                # Exponential backoff with jitter
                if attempt < self.max_retries:
                    backoff_ms = self.base_backoff_ms * (2 ** (attempt - 1))
                    jitter_ms = random.uniform(0, backoff_ms * 0.5)
                    time.sleep((backoff_ms + jitter_ms) / 1000.0)

        # All retries exhausted — try fallback
        fallback_result = self._execute_fallback(worm_type, worm_instance, last_error)

        return {
            "result": fallback_result,
            "outcome": "degraded",
            "recovery": {
                "strategy": "fallback",
                "attempts": self.max_retries,
                "last_error": last_error,
            },
        }

    def _execute_fallback(self, worm_type: str, worm_instance: Any,
                           error: (str | None)) -> (dict | None):
        """Execute fallback strategy when all retries fail.

        Returns degraded/safe result rather than crashing.
        """
        # Try to get minimal status if execute() fails
        if hasattr(worm_instance, 'get_status'):
            try:
                return worm_instance.get_status()
            except Exception as exc:
                note_failure("error_recovery:L216", exc)

        # Fallback: return safe degraded response
        return {
            "worm_type": worm_type,
            "status": "degraded",
            "error": error,
            "fallback": True,
            "timestamp": datetime.now().isoformat(),
        }

    def _is_circuit_open(self, worm_type: str) -> bool:
        """Check if circuit breaker is open for a worm type.
        
        Uses time.monotonic() — immune to system clock changes (NTP, DST).
        """
        with self._lock:
            opened_at = self._circuit_open.get(worm_type)
            if opened_at is None:
                return False
            # Reset if enough time has passed
            if time.monotonic() - opened_at > self.circuit_reset_seconds:
                del self._circuit_open[worm_type]
                self._failure_counts[worm_type] = 0
                return False
            return True

    def _count_recent_failures(self, worm_type: str) -> int:
        """Count failures within the circuit breaker window."""
        cutoff = time.monotonic() - self.circuit_reset_seconds
        with self._lock:
            return sum(1 for t in self._failure_windows.get(worm_type, []) if t > cutoff)

    def _cleanup_failure_windows(self, worm_type: str) -> None:
        """Remove stale failure timestamps outside the window."""
        cutoff = time.monotonic() - self.circuit_reset_seconds
        with self._lock:
            if worm_type in self._failure_windows:
                self._failure_windows[worm_type] = [
                    t for t in self._failure_windows[worm_type] if t > cutoff
                ]

    def _periodic_cleanup(self) -> None:
        """Periodic cleanup of stale data across ALL worm types.
        
        Prevents memory leak for worms that stop being called.
        Runs at most once per circuit_reset_seconds.
        """
        now = time.monotonic()
        if now - self._last_cleanup < self.circuit_reset_seconds:
            return
        self._last_cleanup = now
        cutoff = now - self.circuit_reset_seconds
        with self._lock:
            stale_worms = []
            for worm_type, timestamps in list(self._failure_windows.items()):
                self._failure_windows[worm_type] = [
                    t for t in timestamps if t > cutoff
                ]
                if not self._failure_windows[worm_type]:
                    stale_worms.append(worm_type)
            for worm_type in stale_worms:
                del self._failure_windows[worm_type]
                # Production caller for reset_circuit: a worm whose failure
                # window has fully aged out AND stopped being called gets a
                # clean slate through the same path an operator reset uses
                # (RLock: reset_circuit re-acquires safely).
                self.reset_circuit(worm_type)

    def _record_recovery(self, entry: RecoveryEntry) -> None:
        """Record a recovery attempt."""
        with self._lock:
            self._recovery_history.append(entry)
            self._total_attempts += 1
            if entry.outcome == "success":
                self._total_successes += 1
            if len(self._recovery_history) > 500:
                self._recovery_history = self._recovery_history[-500:]

    def get_circuit_breaker_status(self) -> dict[str, Any]:
        """Get status of all circuit breakers."""
        with self._lock:
            open_circuits = {}
            now = time.monotonic()
            for worm_type, opened_at in list(self._circuit_open.items()):
                if now - opened_at <= self.circuit_reset_seconds:
                    remaining = self.circuit_reset_seconds - (now - opened_at)
                    open_circuits[worm_type] = {
                        "opened_at": datetime.now().isoformat(),  # Display timestamp
                        "remaining_seconds": round(remaining, 1),
                        "failure_count": self._failure_counts.get(worm_type, 0),
                    }
                else:
                    del self._circuit_open[worm_type]

            return {
                "open_circuits": open_circuits,
                "total_open": len(open_circuits),
                "threshold": self.circuit_threshold,
                "reset_seconds": self.circuit_reset_seconds,
            }

    def reset_circuit(self, worm_type: str) -> dict[str, Any]:
        """Manually reset a circuit breaker after fixing the worm."""
        with self._lock:
            self._circuit_open.pop(worm_type, None)
            self._failure_counts[worm_type] = 0
            self._failure_windows.pop(worm_type, None)
        return {
            "worm_type": worm_type,
            "reset": True,
            "timestamp": datetime.now().isoformat(),
        }

    def get_stats(self) -> dict[str, Any]:
        """Get recovery statistics."""
        with self._lock:
            return {
                "version": self.VERSION,
                "total_attempts": self._total_attempts,
                "total_successes": self._total_successes,
                "success_rate": round(
                    self._total_successes / max(self._total_attempts, 1), 4
                ),
                "worm_failure_counts": dict(self._failure_counts),
                "circuit_breaker": self.get_circuit_breaker_status(),
                "recent_recoveries": [
                    {
                        "worm": e.worm_type,
                        "outcome": e.outcome,
                        "attempts": e.attempt,
                        "strategy": e.strategy,
                    }
                    for e in self._recovery_history[-10:]
                ],
            }

    def execute(self) -> dict[str, Any]:
        """Standard worm execute — returns recovery statistics (no I/O)."""
        return self.get_stats()
