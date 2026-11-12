# coding: utf-8
"""
Circuit Breaker Pattern for AZAZAEL Production Worms
====================================================
Implements open/half-open/closed states with:
  - Exponential backoff retry
  - Dead letter queue for failed executions
  - Time-windowed error-rate tripping (Step 4, CODEBASE_AUDIT_TODO.md P1-12)
  - Cancellation tokens for timeout handling
  - Health scoring per worm
"""
import contextlib
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List

__all__ = [
    "CircuitBreaker", "BreakerRegistry", "CircuitConfig", "CircuitState",
    "DeadLetter", "breaker_registry", "replan_from_dead_letters",
    "supervise_drift",
]

log = logging.getLogger(__name__)

#: Fail-open swallows recorded when the runtime recorder is unavailable. An
#: unrecorded swallow is indistinguishable from success, so every handler here
#: records first (a breaker must never break the thing it guards, but its
#: degradation must not be invisible either).
_SWALLOWED: List[str] = []


try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # runtime absent: stay importable
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        _SWALLOWED.append("%s:no-recorder" % where)

#: EP383 — the divergence signal the breaker terminates on. Module-level (an
#: in-function import is a ratchet failure) and fail-open: the breaker must
#: stay importable without the gate, and only terminate on a PROVEN drift.
try:
    from CORE.verification_gate import confidence_divergence_check as _drift_check
except ImportError as exc:                               # gate absent: supervision off
    note_failure("circuit_breaker:drift-import", exc)
    _drift_check = None


def _note_failure(where: str, exc: BaseException | None = None) -> None:
    """Record a fail-open swallow. Never raises."""
    try:
        note_failure(where, exc)
    except Exception as _e:
        _SWALLOWED.append("%s:%s" % (where, type(_e).__name__))


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitConfig:
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_max_requests: int = 3
    retry_base_delay: float = 0.1
    retry_max_delay: float = 60.0
    retry_backoff: float = 2.0
    dead_letter_max: int = 1000
    #: Step 4 (CODEBASE_AUDIT_TODO.md P1-12) - time-windowed error-rate trip.
    #: The cumulative `failure_threshold` counts failures regardless of age, so
    #: a slowly-failing dependency trips it only after a long run of failures; a
    #: "90% in 10s" rule catches the dependency that is failing *now*. Enabled
    #: by default because that is the production intent, and `min_samples` keeps
    #: one early failure from reading as a 100% rate.
    error_rate_enabled: bool = True
    error_rate_threshold: float = 0.9
    error_rate_window: float = 10.0
    error_rate_min_samples: int = 10


#: EP129 phase taxonomy (Agent Failure Taxonomy, 2502.05915). A failure is a
#: PLANNING, EXECUTION or RESPONSE failure and each one needs a different
#: repair; grouping the dead-letter queue by phase is what makes that possible.
PHASES = ("planning", "execution", "response")
#: The honest default: the caller did not say which phase failed. Never
#: guessed, because a wrong phase produces a wrong replan.
PHASE_UNKNOWN = "unknown"


@dataclass
class DeadLetter:
    worm_name: str
    args: tuple
    kwargs: dict
    error: str
    timestamp: float = field(default_factory=time.time)
    retry_count: int = 0
    #: EP129. Defaulted, so every pre-existing construction site keeps working
    #: unchanged; a caller that declares a phase (see
    #: `CircuitBreaker.declared_phase`) gets it recorded here.
    phase: str = PHASE_UNKNOWN


class CircuitBreaker:
    """Production circuit breaker with retry and dead letter queue."""

    def __init__(self, name: str = "default", config: (CircuitConfig | None) = None,
                 threshold: (int | None) = None, cooldown: (float | None) = None):
        self.name = name
        if config is None:
            config = CircuitConfig()
        if threshold is not None:
            config.failure_threshold = threshold
        if cooldown is not None:
            config.recovery_timeout = cooldown
        self.config = config
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0.0
        self.last_success_time = 0.0
        self.half_open_requests = 0
        self.dead_letters: deque = deque(maxlen=self.config.dead_letter_max)
        #: Sliding window of (timestamp, ok) samples for the error-rate trip.
        #: Pruned by age on every record and every read, so it never holds more
        #: than `error_rate_window` seconds of traffic. Guarded by `_lock`, the
        #: same lock as the counters it is judged against.
        self._events: deque = deque()
        # Operator-trip provenance. `test_circuit_breaker_integration.py` has
        # called `force_trip()` since the file was written, but the method was
        # never implemented, so the documented operator channel did not exist.
        self.trip_reason = ""
        self.tripped_at = 0.0
        self.tripped_by_operator = False
        # EP383: provenance for trips caused by a safety-drift signal, kept
        # beside the operator-trip provenance so `stats()` surfaces both.
        self.drift_trips: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        # EP129: the phase currently being attempted, set by declared_phase().
        # Empty means "the caller did not say" and stays PHASE_UNKNOWN in the
        # dead letter rather than being guessed.
        self._phase = ""
        self._phase_lock = threading.Lock()

    @property
    def health_score(self) -> float:
        """0.0 = dead, 1.0 = perfect health. Thread-safe via _lock."""
        with self._lock:
            total = self.failure_count + self.success_count
            if total == 0:
                return 1.0
            return self.success_count / total

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self.state == CircuitState.OPEN

    def cancel_all(self):
        """Cancel all pending operations. Thread-safe."""
        with self._lock:
            self._cancelled.set()

    def force_trip(self, reason: str = "operator") -> dict:
        """Trip the breaker OPEN by operator decision. Thread-safe.

        An automatic trip needs `failure_threshold` real failures; an operator
        needs a way to take an unhealthy dependency out of service *now*
        (a known-bad deploy, a provider being drained, a security hold).
        Without this the only path to OPEN was to provoke real failures.

        This is deliberately a state change and not a fake failure: no dead
        letter is written, because the DLQ is a record of failed executions
        and a trip has no execution to record. The provenance lives in
        `trip_reason` / `tripped_at`, which `stats()` surfaces, so the
        decision is still auditable.

        `last_failure_time` is stamped so the OPEN -> HALF_OPEN cooldown is
        measured from the trip, which is what makes the trip recoverable.
        """
        reason = str(reason or "operator")[:200]
        with self._lock:
            self.state = CircuitState.OPEN
            self.last_failure_time = time.time()
            self.half_open_requests = 0
            # Keep the counters consistent with the state, so health_score and
            # the failure threshold cannot contradict `state == OPEN`.
            self.failure_count = max(self.failure_count,
                                    self.config.failure_threshold)
            self.trip_reason = reason
            self.tripped_at = self.last_failure_time
            self.tripped_by_operator = True
            log.warning(f"CircuitBreaker({self.name}): FORCE TRIP -> OPEN ({reason})")
        return {"success": True, "name": self.name, "state": self.state.value,
                "reason": reason, "retry_in": self.config.recovery_timeout}

    @contextlib.contextmanager
    def declared_phase(self, phase: str):
        """Declare the phase of the work inside this block (EP129).

        Failures recorded inside the block carry this phase, so the dead-letter
        queue can be grouped into planning / execution / response failures -
        each of which needs a different repair. An unrecognised name is
        recorded as PHASE_UNKNOWN rather than coerced into a real phase.
        Nesting restores the previous value. Never raises.
        """
        name = str(phase or "").strip().lower()
        if name not in PHASES:
            name = PHASE_UNKNOWN
        previous = ""
        try:
            with self._phase_lock:
                previous = self._phase
                self._phase = name
        except Exception as exc:
            note_failure("circuit_breaker:L195", exc)
            previous = ""
        try:
            yield name
        finally:
            try:
                with self._phase_lock:
                    self._phase = previous
            except Exception as exc:              # noqa: BLE001 — phase is advisory
                _note_failure("circuit_breaker:phase-restore", exc)

    def hold(self) -> bool:
        """True while an operator trip is still in force.

        An operator trip must not be silently undone by the ordinary
        HALF_OPEN -> CLOSED success path: `reset()` is the documented way to
        release a hold, so callers that want automatic recovery can read this
        and decide. Thread-safe.
        """
        with self._lock:
            return bool(self.tripped_by_operator)

    def _prune_events_locked(self, now: float) -> None:
        """Drop samples older than the window. Caller holds `_lock`."""
        cutoff = now - max(0.0, float(self.config.error_rate_window))
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def _windowed_failure_rate_locked(self, now: float) -> "float | None":
        """Failure rate inside the window, or None below `min_samples`.

        None is a real answer, not a zero: fewer than `error_rate_min_samples`
        events is not evidence of a healthy dependency, and returning 0.0 would
        make "no data yet" indistinguishable from "no failures".
        """
        self._prune_events_locked(now)
        total = len(self._events)
        if total < max(0, int(self.config.error_rate_min_samples)):
            return None
        failures = sum(1 for _ts, _ok in self._events if not _ok)
        return failures / total

    def _rate_trip_locked(self, now: float) -> bool:
        """True when the windowed rate alone should trip OPEN. Caller holds `_lock`."""
        if not self.config.error_rate_enabled:
            return False
        rate = self._windowed_failure_rate_locked(now)
        return rate is not None and rate >= float(self.config.error_rate_threshold)

    def windowed_failure_rate(self) -> "float | None":
        """Failure rate over `error_rate_window`, or None below `min_samples`.

        Thread-safe. This is the read half of the rate trip, exposed so a caller
        or a monitor can see the number the breaker tripped on instead of
        inferring it from the state transition.
        """
        with self._lock:
            return self._windowed_failure_rate_locked(time.time())

    def supervise_drift(self, explorer: List[Dict[str, Any]],
                        validator: List[Dict[str, Any]],
                        reason: str = "safety-drift",
                        max_mean_div: float = 0.15,
                        max_max_div: float = 0.30) -> Dict[str, Any]:
        """Trip OPEN when an explorer/validator pair diverges (EP383).

        Why this exists: `verification_gate.confidence_divergence_check`
        detects the confident-but-wrong case, but a detected drift terminated
        nothing — the finding was a verdict with no consequence. Termination
        belongs on the breaker (that is what `force_trip` is for), so this is
        the consequence: a failed divergence check trips the breaker OPEN and
        the provenance is recorded in `drift_trips` (surfaced by `stats()`).

        Fail-open in the same sense as the rest of this module: a missing gate
        module or a raising check yields a named gap with `checked=False`; it
        never raises, and it never trips on uncertainty — an unproven drift is
        not a drift.

        Returns {"checked", "passed", "tripped", "reason", "mean_div",
        "diverged_ids", "gaps"}.
        """
        out: Dict[str, Any] = {"checked": False, "passed": None,
                               "tripped": False, "reason": "",
                               "mean_div": None, "diverged_ids": [],
                               "gaps": []}
        if _drift_check is None:
            out["gaps"].append("verification-gate-unavailable")
            return out
        try:
            check = _drift_check(explorer or [], validator or [],
                                 max_mean_div=max_mean_div,
                                 max_max_div=max_max_div)
        except Exception as exc:                  # noqa: BLE001 — supervision is best-effort
            _note_failure("circuit_breaker:drift-check", exc)
            out["gaps"].append(f"divergence-check-failed:{type(exc).__name__}")
            return out
        out["checked"] = True
        out["passed"] = bool(check.get("passed"))
        out["mean_div"] = check.get("mean_div")
        out["diverged_ids"] = [str(i) for i in (check.get("diverged_ids") or [])]
        if out["passed"]:
            return out
        trip_reason = "%s:diverged=%s" % (
            str(reason or "safety-drift")[:160],
            ",".join(out["diverged_ids"]) or "-")
        self.force_trip(trip_reason)
        out["tripped"] = True
        out["reason"] = trip_reason
        with self._lock:
            self.drift_trips.append({"reason": trip_reason,
                                     "diverged_ids": list(out["diverged_ids"]),
                                     "mean_div": out["mean_div"],
                                     "at": self.tripped_at})
        return out

    def reset(self):
        """Reset the circuit breaker. Thread-safe.

        Clears the cancellation flag, resets state to CLOSED,
        and zeros failure/request counts. Also releases an operator trip —
        this is the documented way to clear a `force_trip()` hold.
        """
        with self._lock:
            self._cancelled.clear()
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.half_open_requests = 0
            # The window is count-and-time state, like failure_count: a reset
            # that kept it would leave the rate rule tripping on history the
            # operator already cleared.
            self._events.clear()
            self.trip_reason = ""
            self.tripped_at = 0.0
            self.tripped_by_operator = False

    def execute(self, func: Callable, *args, **kwargs) -> Any:
        """Execute with circuit breaker protection."""
        if self._cancelled.is_set():
            raise RuntimeError(f"CircuitBreaker({self.name}): cancelled")

        with self._lock:
            if self.state == CircuitState.OPEN:
                if time.time() - self.last_failure_time >= self.config.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_requests = 0
                    log.info(f"CircuitBreaker({self.name}): OPEN -> HALF_OPEN")
                else:
                    raise RuntimeError(
                        f"CircuitBreaker({self.name}): OPEN — "
                        f"retry in {self.config.recovery_timeout - (time.time() - self.last_failure_time):.0f}s"
                    )

            if self.state == CircuitState.HALF_OPEN:
                if self.half_open_requests >= self.config.half_open_max_requests:
                    raise RuntimeError(
                        f"CircuitBreaker({self.name}): HALF_OPEN limit reached"
                    )
                self.half_open_requests += 1

        try:
            result = func(*args, **kwargs)
            with self._lock:
                now = time.time()
                self.success_count += 1
                self.last_success_time = now
                self._events.append((now, True))
                self._prune_events_locked(now)
                if self.state == CircuitState.HALF_OPEN:
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    # A successful probe is evidence the dependency recovered,
                    # so the pre-trip window is stale evidence. Keeping it would
                    # re-trip the rate rule on the very next failure and make
                    # recovery impossible.
                    self._events.clear()
                    log.info(f"CircuitBreaker({self.name}): HALF_OPEN -> CLOSED")
            return result
        except Exception as e:
            tripped = False
            with self._lock:
                now = time.time()
                self.failure_count += 1
                self.last_failure_time = now
                self._events.append((now, False))
                self._prune_events_locked(now)
                if (self.state == CircuitState.CLOSED and
                        self.failure_count >= self.config.failure_threshold):
                    self.state = CircuitState.OPEN
                    tripped = True
                    log.warning(f"CircuitBreaker({self.name}): CLOSED -> OPEN ({self.failure_count} failures)")
                elif (self.state == CircuitState.CLOSED and
                        self._rate_trip_locked(now)):
                    self.state = CircuitState.OPEN
                    tripped = True
                    log.warning(
                        f"CircuitBreaker({self.name}): CLOSED -> OPEN "
                        f"({self._windowed_failure_rate_locked(now):.2%} error rate "
                        f"over {self.config.error_rate_window:g}s)")
                elif self.state == CircuitState.HALF_OPEN:
                    self.state = CircuitState.OPEN
                    tripped = True
                    log.warning(f"CircuitBreaker({self.name}): HALF_OPEN -> OPEN")

                # Best-effort: only a declared phase, never a guess.
                failure_phase = self._phase or PHASE_UNKNOWN
                self.dead_letters.append(DeadLetter(
                    worm_name=self.name,
                    args=args, kwargs=kwargs,
                    error=str(e),
                    phase=failure_phase,
                ))
            # Verify-before-accept audit on the OPEN trip: the transition gets a
            # verification citation, but the verdict NEVER gates control flow
            # (fail-open house rule — the trip stands regardless). Outside the
            # lock on purpose: verification is a memory write and must not
            # serialise callers. Subject-only: no detectors are armed with
            # fabricated inputs, so this is an audit row, not a judgment.
            if tripped:
                try:
                    from CORE.technique_runtime import verify_boundary as _verify
                    _verify(f"breaker:{self.name}:OPEN-trip")
                except Exception as exc:
                    _note_failure("circuit_breaker:trip-verify", exc)
            # EP113 + EP294: the failure is now recorded, which makes this the
            # one place a lesson can be written from certain knowledge. Called
            # OUTSIDE the breaker lock (a reflection is a memory write, and
            # holding _lock across it would serialise every caller) and fully
            # fail-open, so it can never change this method's behaviour.
            _reflect_on_failure(self, str(e), failure_phase)
            raise

    def retry_with_backoff(self, func: Callable, *args, max_retries: int = 3, **kwargs) -> Any:
        """Execute with exponential backoff retry."""
        last_error = None
        delay = self.config.retry_base_delay

        for attempt in range(max_retries + 1):
            if self._cancelled.is_set():
                raise RuntimeError(f"CircuitBreaker({self.name}): cancelled")
            try:
                return self.execute(func, *args, **kwargs)
            except RuntimeError:
                raise
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    log.warning(
                        f"CircuitBreaker({self.name}): attempt {attempt+1}/{max_retries+1} "
                        f"failed, retrying in {delay:.1f}s"
                    )
                    time.sleep(delay)
                    delay = min(delay * self.config.retry_backoff, self.config.retry_max_delay)

        raise last_error or RuntimeError(f"CircuitBreaker({self.name}): all retries exhausted")

    def stats(self) -> dict:
        """Return circuit breaker statistics. Thread-safe via _lock."""
        with self._lock:
            # Compute health inline — calling self.health_score here would
            # re-acquire the non-reentrant _lock and self-deadlock.
            total = self.failure_count + self.success_count
            health = 1.0 if total == 0 else self.success_count / total
            rate = self._windowed_failure_rate_locked(time.time())
            return {
                "name": self.name,
                "state": self.state.value,
                "failures": self.failure_count,
                "successes": self.success_count,
                "health": round(health, 3),
                "dead_letters": len(self.dead_letters),
                "last_failure": self.last_failure_time,
                "cancelled": self._cancelled.is_set(),
                "trip_reason": self.trip_reason,
                "tripped_at": self.tripped_at,
                "held": self.tripped_by_operator,
                "drift_trips": list(self.drift_trips),
                "windowed_failure_rate": (
                    None if rate is None else round(rate, 4)),
            }


# --- Global Circuit Breaker Registry ---

class BreakerRegistry:
    """Registry of all circuit breakers for monitoring."""

    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get(self, name: str, config: (CircuitConfig | None) = None) -> CircuitBreaker:
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(name, config)
            return self._breakers[name]

    def all_stats(self) -> list[dict]:
        return [b.stats() for b in self._breakers.values()]

    def cancel_all(self):
        """Cancel all pending operations across ALL breakers. Thread-safe."""
        with self._lock:
            for b in self._breakers.values():
                b.cancel_all()

    def reset_all(self):
        """Reset all circuit breakers to closed state. Thread-safe."""
        with self._lock:
            for b in self._breakers.values():
                b.reset()

    # -- Dead Letter Queue Monitoring ------------------------------

    def get_dead_letters(self, name: (str | None) = None,
                         since: float = 0) -> list[DeadLetter]:
        """Get dead letters, optionally filtered by breaker name and time.
        
        Returns chronologically ordered (most recent first) dead letters
        for the specified breaker, or all breakers if name is None.
        Thread-safe: acquires both registry and per-breaker locks.
        """
        letters = []
        with self._lock:
            names = [name] if name else list(self._breakers.keys())
            for n in names:
                breaker = self._breakers.get(n)
                if breaker:
                    with breaker._lock:
                        for dl in list(breaker.dead_letters):
                            if dl.timestamp >= since:
                                letters.append(dl)
        # Most recent first
        letters.sort(key=lambda x: x.timestamp, reverse=True)
        return letters

    def get_dead_letter_summary(self, lookback_seconds: float = 3600) -> dict[str, Any]:
        """Get a summary of dead letters grouped by breaker.
        
        Args:
            lookback_seconds: How far back to include recent errors (default 1 hour)
        
        Returns:
            dict with total count, per-breaker counts, and most recent errors
        """
        summary: dict[str, Any] = {
            "total_dead_letters": 0,
            "per_breaker": {},
            "recent_errors": [],
        }
        cutoff = time.time() - lookback_seconds
        
        with self._lock:
            for name, breaker in self._breakers.items():
                n_letters = len(breaker.dead_letters)
                if n_letters > 0:
                    summary["per_breaker"][name] = n_letters
                    summary["total_dead_letters"] += n_letters
                    # Collect recent errors
                    for dl in breaker.dead_letters:
                        if dl.timestamp >= cutoff:
                            summary["recent_errors"].append({
                                "breaker": name,
                                "error": dl.error[:120],
                                "timestamp": dl.timestamp,
                                "retry_count": dl.retry_count,
                            })
        
        # Limit recent errors to 20
        summary["recent_errors"] = sorted(
            summary["recent_errors"],
            key=lambda x: x["timestamp"], reverse=True
        )[:20]
        
        return summary

    def purge_dead_letters(self, name: (str | None) = None):
        """Clear dead letters for a specific breaker, or all if name is None."""
        with self._lock:
            if name:
                breaker = self._breakers.get(name)
                if breaker:
                    with breaker._lock:
                        breaker.dead_letters.clear()
            else:
                for breaker in self._breakers.values():
                    with breaker._lock:
                        breaker.dead_letters.clear()


breaker_registry = BreakerRegistry()


#: EP129 - signature -> the concrete next-attempt adjustment. Rule-based and
#: deliberately blunt: the point is that a failure produces a DIFFERENT next
#: attempt, not that the advice is clever.
_REPLAN_RULES = (
    ("timeout", "raise the timeout AND shrink the batch - at planning a timeout "
                "means the plan is too large, not that the work is slow"),
    ("timed out", "raise the timeout AND shrink the batch - a large plan is the "
                  "usual cause"),
    ("connection", "treat the dependency as down: let the cooldown elapse, then "
                   "retry as a single HALF_OPEN probe"),
    ("refused", "the dependency is not accepting work - back off, do not "
                "hot-loop the retry"),
    ("memory", "shrink the batch or stream the input; never retry at the same size"),
    ("permission", "STOP retrying - escalate for credentials/scope; a retry "
                   "cannot fix this"),
    ("denied", "STOP retrying - escalate for credentials/scope"),
    ("not found", "resolve the id/path once and cache it before the next attempt"),
    ("no such file", "resolve the path before the next attempt"),
    ("rate limit", "back off and spread the load - the retry itself is the problem"),
    ("429", "back off and spread the load"),
    ("json", "re-request the input in a stricter format before parsing it again"),
    ("parse", "re-request the input in a stricter format before parsing it again"),
    ("assert", "the precondition is wrong, not the work: check the plan's inputs"),
)


def _error_signature(error: str) -> str:
    """Coarse, group-by-able signature for an error string (EP129).

    Errors from the same cause differ in ids, paths and addresses; normalising
    those out is what lets the queue be counted rather than merely listed.
    """
    s = str(error or "").lower()
    s = re.sub(r"0x[0-9a-f]+", "<hex>", s)
    s = re.sub(r"[a-z]:[\\/][^\s,;)]+", "<path>", s)
    s = re.sub(r"['\"][^'\"]{0,120}['\"]", "<str>", s)
    s = re.sub(r"\d+", "<n>", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:160] or "(empty)"


def replan_from_dead_letters(registry=None, limit: int = 50) -> dict:
    """Group dead letters by phase and signature and propose the replan (EP129).

    Why this exists: the dead-letter queue recorded failures that nothing read.
    `get_dead_letters()` and `get_dead_letter_summary()` counted letters but no
    code turned a recorded failure into a DIFFERENT next attempt. This is that
    consumer - the missing half of the failure taxonomy.

    Returns {n_letters, phases, signatures, worst, replan, gaps}. `replan` is
    an ordered list of concrete adjustments, worst signature first. Fail-open:
    an unreadable queue yields zeros plus a named gap, never an exception.
    """
    out: dict[str, Any] = {"n_letters": 0, "phases": {}, "signatures": {},
                           "worst": {}, "replan": [], "gaps": []}
    try:
        reg = registry if registry is not None else breaker_registry
        letters = list(reg.get_dead_letters() or [])[:max(1, int(limit))]
    except Exception as exc:
        note_failure("circuit_breaker:L510", exc)
        out["gaps"].append(f"unreadable-dlq:{type(exc).__name__}")
        return out

    out["n_letters"] = len(letters)
    phases: dict[str, int] = out["phases"]
    sigs: dict[str, int] = out["signatures"]
    for dl in letters:
        phase = str(getattr(dl, "phase", PHASE_UNKNOWN) or PHASE_UNKNOWN)
        phases[phase] = phases.get(phase, 0) + 1
        sig = _error_signature(getattr(dl, "error", ""))
        sigs[sig] = sigs.get(sig, 0) + 1

    if sigs:
        worst_sig, worst_n = max(sigs.items(), key=lambda kv: kv[1])
        out["worst"] = {"signature": worst_sig, "count": worst_n}

    for sig, count in sorted(sigs.items(), key=lambda kv: (-kv[1], kv[0])):
        advice = ""
        for needle, hint in _REPLAN_RULES:
            if needle in sig:
                advice = hint
                break
        if not advice:
            advice = ("re-run once with identical inputs to confirm it is "
                      "deterministic; a deterministic failure is in the input")
        out["replan"].append(f"x{count} {sig[:70]} -> {advice}")

    if not letters:
        out["gaps"].append("empty-dead-letter-queue")
    return out


def _phase_advice(phase: str, signature: str) -> str:
    """The concrete next-attempt adjustment for a recorded failure (EP129).

    Rule-based and deliberately blunt, like `_REPLAN_RULES`: the point is that a
    failure produces a DIFFERENT next attempt, not that the advice is clever.
    The phase decides the fallback when no signature rule matches, because the
    same error text needs a different repair at planning than at response.
    """
    for needle, hint in _REPLAN_RULES:
        if needle in signature:
            return hint
    if phase == "planning":
        return ("re-plan before retrying: a planning failure means the step list, "
                "not the work, is wrong")
    if phase == "response":
        return ("re-check the response shape/contract before retrying - the work "
                "may have succeeded and the answer been misread")
    return ("re-run once with identical inputs to confirm it is deterministic; "
            "a deterministic failure is in the input, not in the retry")


def _breaker_dlq_view(breaker: "CircuitBreaker") -> Any:
    """A registry-shaped view over ONE breaker's dead letters.

    `replan_from_dead_letters` takes a registry. Scoping it to the breaker that
    just failed keeps the replan cheap (no whole-registry scan on a failure path)
    and relevant (the advice is about this failure, not the fleet's).
    """
    class _View:
        def get_dead_letters(self):
            with breaker._lock:
                return sorted(breaker.dead_letters,
                              key=lambda d: d.timestamp, reverse=True)
    return _View()


def _reflect_on_failure(breaker: "CircuitBreaker", error: str,
                        phase: str = PHASE_UNKNOWN) -> None:
    """Write the verbal reflection for a recorded failure (EP113 + EP129/EP294).

    Why this exists: `reflexion.py` was a complete buffer with NO caller, so no
    failed attempt in this tree produced a lesson and none was ever recalled.
    The dead-letter append in `execute` is the one place a failure is known for
    certain, so the reflection is written there - one per recorded failure, the
    same granularity as the DLQ row, so the two can never disagree.

    `what_next` comes from the error signature and the declared phase; when the
    same signature has failed more than once, the scoped replan is consulted so
    the lesson is the actual next-attempt adjustment rather than a restatement.

    Fail-open by construction: the import is lazy, the body is guarded, and the
    caller ignores the result. A reflection must never affect the breaker.
    """
    try:
        signature = _error_signature(error)
        advice = _phase_advice(phase, signature)
        try:
            if breaker.failure_count >= 2:
                scoped = replan_from_dead_letters(_breaker_dlq_view(breaker),
                                                  limit=10)
                first = (scoped.get("replan") or [None])[0]
                if first:
                    advice = str(first)
        except Exception as exc:                      # noqa: BLE001 — advice optional
            _note_failure("circuit_breaker:reflect-advice", exc)
        from CORE.technique_runtime import reflect_on_failure as _reflect
        _reflect(
            question=f"{breaker.name} failed ({phase})",
            what_failed=f"{phase}: {signature}"[:300],
            what_next=advice[:600],
            decision_id=f"breaker:{breaker.name}")
    except Exception as exc:
        # Fail-open on purpose: a reflection must never affect the breaker.
        # Recorded so the swallow is observable instead of success-shaped.
        _note_failure("circuit_breaker:reflect", exc)
        return


def supervise_drift(name: str, explorer: List[Dict[str, Any]],
                    validator: List[Dict[str, Any]],
                    registry=None, reason: str = "safety-drift") -> Dict[str, Any]:
    """Supervise action safety for one breaker by name (EP383).

    The one-line hook for a caller that already holds an explorer/validator
    confidence pair: it resolves the breaker (creating it if absent, exactly as
    `BreakerRegistry.get` does) and delegates to
    `CircuitBreaker.supervise_drift`. Fail-open: an unusable registry yields a
    named gap, never an exception.
    """
    out: Dict[str, Any] = {"checked": False, "passed": None, "tripped": False,
                           "reason": "", "gaps": []}
    try:
        reg = registry if registry is not None else breaker_registry
        breaker = reg.get(name)
    except Exception as exc:                      # noqa: BLE001 — supervision is best-effort
        _note_failure("circuit_breaker:supervise-registry", exc)
        out["gaps"].append(f"registry-unavailable:{type(exc).__name__}")
        return out
    return breaker.supervise_drift(explorer, validator, reason=reason)


if __name__ == "__main__":
    # Quick test
    cb = CircuitBreaker("test_worm", CircuitConfig(failure_threshold=2, recovery_timeout=2))

    def flaky_func(succeed: bool):
        if not succeed:
            raise ValueError("Simulated failure")
        return "OK"

    for i in range(3):
        try:
            result = cb.execute(flaky_func, False)
            print(f"  Attempt {i+1}: {result}")
        except Exception as e:
            print(f"  Attempt {i+1}: {e}")

    print(f"\n  State: {cb.state.value}, Health: {cb.health_score:.2f}")
    print(f"  Dead letters: {len(cb.dead_letters)}")
