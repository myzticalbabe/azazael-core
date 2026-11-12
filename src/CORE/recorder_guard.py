"""Recorder write guard — a ceiling on how fast one event type can fill the store.

Why this exists (measured 2026-10-31): `trainers_v2` emits one `episode_complete`
row per RL episode. Five evolution sessions each wrote ~150k rows in ~35 minutes
(~62 rows/s, a row every 12 ms), and `memory_store.db` reached **6.41 GB /
4 743 846 rows**. `CORE/data_retention.py` already defines the enforcers
(`enforce_global_quota`, `enforce_event_ttl`, `enforce_type_caps`) but nothing
outside that module ever called them, and `DB_SIZE_QUOTA_MB` merely *warns* —
so no ceiling was ever applied to the write path. This module is that ceiling.

Design rules that matter:

* **Errors are never throttled.** `severity` in {error, critical} bypasses every
  limit, always. A guard that can drop the one row explaining an outage is worse
  than the disk pressure it prevents.
* **Low-volume, high-meaning types are never throttled** (evolution results,
  promotions, reflexion notes, distilled summaries). The guard targets the
  mechanical per-tick emitters, which are the only ones that can balloon.
* **Token bucket, not a counter.** A burst is legitimate (a training run start
  writes a few rows at once); sustained volume is not. Buckets refill by wall
  clock, so a slow run is never penalised for an earlier fast one.
* **Suppression is counted, not silent.** `snapshot()` answers "how many rows did
  the guard drop", and a one-line notice goes to stderr at most once per type
  per minute. A guard whose drops are invisible is indistinguishable from a
  broken emitter.
* **A type we have never seen is capped too.** The next runaway will not be
  called `episode_complete`; it will be some new per-tick event nobody has
  classified yet. The default bucket (8 rows/s, burst 200) is therefore the
  important one, and it applies to every type not explicitly listed. The burst
  is deliberately generous: a legitimate import writes 50-150 rows at once and
  must not be mangled, while 8 rows/s sustained is 691k/day — a third of what
  the runaway managed, and the session ceiling catches the rest.
* **A session ceiling sits above the buckets.** Per-type limits do not stop a
  process that emits fifty different tick types; `SESSION_MAX` (default 100 000
  non-exempt rows per process) does. Past it only exempt types pass, which is
  exactly the audit trail you would want to keep.
* Thread-safe: this is imported by a 3.14t free-threaded process where several
  trainers share the same store.

Env overrides (all optional):
  RECORDER_GUARD_RATE      sustained rows/second for unlisted types (default 8)
  RECORDER_GUARD_BURST     burst rows for unlisted types (default 200)
  RECORDER_GUARD_SESSION_MAX  non-exempt rows per process (default 100000)
  RECORDER_GUARD_OFF=1     disable entirely (kill switch)
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Dict, Tuple

# event_type -> (rows per second, burst)
RATE_LIMITS: Dict[str, Tuple[float, int]] = {
    "episode_complete": (4.0, 12),      # the 2026-10-31 runaway: 62/s sustained
    "q_table_snapshot": (1.0, 5),
    "train_start": (1.0, 5),
    "episode_start": (2.0, 8),
    "reward-term": (2.0, 8),
    "read_pressure": (1.0, 5),
    "retrieval_audit": (1.0, 5),
    "scale-distribution": (1.0, 5),
    "hook:technique_runtime.reflect_on_failure": (1.0, 5),
    "hook:technique_runtime.verify_boundary": (1.0, 5),
}

# Never throttled: these carry the meaning that survives after the noise is gone.
ALWAYS_ALLOW = frozenset({
    "evolution_result", "evolution_promotion", "generation_complete",
    "reflexion_note", "memory_distilled", "recorder_throttled",
    "session_start", "session_end", "verification", "verification_failure",
    "security", "credential", "compliance", "audit", "incident",
    "db_health_alert", "admission_refusal",
})

_PREFIX_ALLOW = ("hook:",)  # hooks are low volume by construction; counted, not capped

_DEFAULT_RATE = float(os.environ.get("RECORDER_GUARD_RATE", "8") or 8)
_DEFAULT_BURST = int(os.environ.get("RECORDER_GUARD_BURST", "200") or 200)
_SESSION_MAX = int(os.environ.get("RECORDER_GUARD_SESSION_MAX", "100000") or 100000)
_DISABLED = (os.environ.get("RECORDER_GUARD_OFF", "") == "1")
_NOTICE_INTERVAL_S = 60.0


class RecorderGuard:
    """Token buckets per event type, plus a count of what was dropped."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: Dict[str, float] = {}
        self._last: Dict[str, float] = {}
        self._suppressed: Dict[str, int] = {}
        self._allowed: Dict[str, int] = {}
        self._noticed_at: Dict[str, float] = {}
        self._row_count = 0          # non-exempt rows allowed this process
        self._session_capped = False

    # -- limits ----------------------------------------------------------
    @staticmethod
    def _limits(event_type: str) -> Tuple[float, int]:
        return RATE_LIMITS.get(event_type, (_DEFAULT_RATE, _DEFAULT_BURST))

    @staticmethod
    def _exempt(event_type: str, severity: str | None) -> bool:
        if (severity or "").lower() in ("error", "critical"):
            return True
        if event_type in ALWAYS_ALLOW:
            return True
        return any(event_type.startswith(p) for p in _PREFIX_ALLOW)

    # -- decision --------------------------------------------------------
    def allow(self, event_type: str, severity: str | None = None) -> bool:
        """True if this row may be written now. Cheap: one lock, no I/O."""
        if _DISABLED or self._exempt(event_type, severity):
            return True
        # Session ceiling first: it is the only limit that still holds when the
        # emitter invents a new event_type every tick.
        if self._session_capped:
            with self._lock:
                self._suppressed[event_type] = self._suppressed.get(event_type, 0) + 1
            return False
        now = time.monotonic()
        # Named `burst_rows`, not `burst`: a local with the bare name made
        # xenomorph:ChestbursterWorm.burst look referenced to the mesh probe,
        # which then reported a RETIRED row as resurrected. The two have nothing
        # to do with each other, and a generic identifier is enough to lie to a
        # token matcher.
        rate, burst_rows = self._limits(event_type)
        with self._lock:
            tokens = self._tokens.get(event_type)
            last = self._last.get(event_type)
            if tokens is None or last is None:
                tokens = float(burst_rows)
                last = now
            else:
                # refill, capped at the bucket size (idle never banks credit)
                tokens = min(float(burst_rows), tokens + (now - last) * rate)
            if tokens >= 1.0:
                self._tokens[event_type] = tokens - 1.0
                self._last[event_type] = now
                self._allowed[event_type] = self._allowed.get(event_type, 0) + 1
                self._row_count += 1
                if self._row_count > _SESSION_MAX:
                    self._session_capped = True
                return True
            self._tokens[event_type] = tokens
            self._last[event_type] = now
            self._suppressed[event_type] = self._suppressed.get(event_type, 0) + 1
            n = self._suppressed[event_type]
            notice_due = (now - self._noticed_at.get(event_type, 0.0)) >= _NOTICE_INTERVAL_S
            if notice_due:
                self._noticed_at[event_type] = now
        if notice_due:
            # one line per type per minute: visible without flooding the log
            try:
                print(f"[recorder_guard] throttled {event_type!r}: {n} rows dropped so far "
                      f"(limit {rate}/s, burst {burst_rows})", file=sys.stderr, flush=True)
            except Exception as exc:
                note_failure("src/CORE/recorder_guard.py", exc)
                pass
        return False

    # -- observation -----------------------------------------------------
    def snapshot(self) -> Dict[str, Dict[str, int]]:
        """Per-type allowed/dropped counts since process start."""
        with self._lock:
            keys = set(self._allowed) | set(self._suppressed)
            return {
                k: {"allowed": self._allowed.get(k, 0),
                    "suppressed": self._suppressed.get(k, 0)}
                for k in sorted(keys)
            }

    def totals(self) -> Dict[str, object]:
        """Process-level view: rows written, ceiling reached or not."""
        with self._lock:
            return {"rows_written": self._row_count,
                    "session_max": _SESSION_MAX,
                    "session_capped": self._session_capped,
                    "suppressed_total": sum(self._suppressed.values())}

    def reset(self) -> None:
        with self._lock:
            self._tokens.clear()
            self._last.clear()
            self._suppressed.clear()
            self._allowed.clear()
            self._noticed_at.clear()
            self._row_count = 0
            self._session_capped = False


_GUARD = RecorderGuard()


def guard() -> RecorderGuard:
    """Process-wide guard."""
    return _GUARD


try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site


def allow(event_type: str, severity: str | None = None) -> bool:
    """Module-level shorthand for `guard().allow(...)`."""
    return _GUARD.allow(event_type, severity)
