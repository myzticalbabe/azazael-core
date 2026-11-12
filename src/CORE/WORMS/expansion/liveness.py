# coding: utf-8
"""Liveness obligations — expectations with deadlines, so absence is observable.

Why this module exists
----------------------
The 2026-09-16 per-function audit found four defects and every one of them was
invisible to the existing monitors. They had nothing in common except this:

    * a generation of offspring was silently discarded by a lost update,
    * an objective was never evaluated because an elite slot overflowed,
    * a weight vector that did not sum to 1 was used for every comparison, and
    * a checkpoint was never persisted while `save_checkpoint` reported success.

None of them *happened*. Each was an expected event that did **not** occur, and
every monitor in the mesh is occurrence-driven: it renders what happened. More
occurrence telemetry cannot catch a defect defined by an absence -- that is not
a gap in coverage, it is a category error, and it is why `fence_report()`'s
rejection counter and `silence_report()`'s tripped tags are the only two places
in the architecture that record a negative at all.

The insight
-----------
Invert the reporting direction. Instead of the monitor asking "what occurred?",
the code that is *supposed* to cause something declares an expectation with a
deadline (`require` for a recurring beat, `expect` for a one-shot). Fulfilling
it discharges the obligation. What the monitor reads is the set of obligations
that are **still outstanding**, and an overdue one is a first-class fault:

    require("spine.cycle.commit", every=120)   # declared once, by the producer
    ...                                        # cycle runs
    beat("spine.cycle.commit")                 # discharged on every commit

Now "the fabric is healthy" is a claim that can be *checked* rather than
inferred, because a monitor that sees zero overdue obligations AND zero
declared obligations has learned something very different from a monitor that
sees zero overdue out of twelve declared.

That distinction is the second half of the insight, and it is the half that
makes this more than a timeout. An empty overdue list has two causes:

* every obligation is being met, and
* **nobody armed any obligation** -- the producer was deleted, the wiring was
  lost, the process that calls `require()` never started.

Both render identically as "nothing overdue". So the report carries the
declared set and the count ever armed, and `unmonitored()` names the obligations
that were declared and have since stopped being armed. A monitor showing a green
panel with `declared: 0` is showing an unknown, not an all-clear -- which is the
same failure mode as the 118 silent swallows in `silence.py`, one level up.

What this is not
----------------
- Not a scheduler and not a health check. It never runs code and never fixes
  anything; it records that a promised thing did not arrive in time.
- Never raises. A liveness ledger that can fail is worse than none, because it
  would be the component reporting on failure.
- In-process, and deliberately so, with the same reasoning as `fencing.py`: the
  state it observes (a scheduler's tick, a commit, a persisted checkpoint) lives
  in one process. Obligations that must survive a restart belong in
  `spine_registry`, which already persists across process boundaries.

Usage::

    from CORE.WORMS.expansion.liveness import beat, expect, fulfill, require

    require("evolution.generation", every=300)   # declare once, at wiring time

    def evolve(self):
        with obliged("evolution.generation.commit", ttl=60):
            ...                                  # a block that raises, or is
                                                 # never entered, stays overdue
        beat("evolution.generation")

    # the monitor side
    report = liveness_report()
    if not report["healthy"]:
        for miss in report["overdue"]:
            print(miss["name"], miss["kind"], miss["overdue_by"])
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = [
    "expect", "fulfill", "beat", "require", "obliged", "overdue",
    "unmonitored", "liveness_report", "liveness_reset", "LivenessLedger",
    "LEDGER",
]

# Distinct obligation names tracked. The mesh has ~1400 functions; more names
# than this means a name is being built from data rather than declared once.
_MAX_NAMES = 512

# Latched misses kept for the report. Bounded for the same reason as every
# other ledger here: a broken loop must not turn the diagnostic into a leak.
_MAX_HISTORY = 256


class LivenessLedger:
    """Declared obligations, their deadlines, and the ones that went unmet.

    Three kinds of miss are distinguished, because they mean different things:

    ``unfulfilled``
        A one-shot was armed with a deadline and the deadline passed. Something
        specific failed to complete.
    ``silent``
        A recurring beat has beaten at least once and then stopped. Something
        that was working has died.
    ``never_beaten``
        A requirement was declared and has never beaten within its own period.
        The producer never started at all -- the most common real cause, and the
        one an occurrence monitor structurally cannot see.
    """

    __slots__ = ("_lock", "_open", "_beats", "_required", "_history",
                 "_seen", "_armed", "_fulfilled", "_beat_count",
                 "_monitored_since", "_declared_ever", "_armed_ever")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._open: dict[str, dict] = {}
        self._beats: dict[str, dict] = {}
        self._required: dict[str, dict] = {}
        self._history: list[dict] = []
        # Distinct (name, kind) misses ever seen, so `misses_total` counts
        # misses rather than polls.
        self._seen: set = set()
        self._armed = 0
        self._fulfilled = 0
        self._beat_count = 0
        self._monitored_since = time.time()
        self._declared_ever: set[str] = set()
        self._armed_ever: set[str] = set()

    # -- declaration -----------------------------------------------------
    def require(self, name: str, every: float = 60.0) -> bool:
        """Declare that ``name`` must be beaten at least every ``every`` seconds.

        Safe to call repeatedly; the tightest period wins. The clock starts now,
        so a producer that fails to start is reported as ``never_beaten`` once
        the period elapses rather than never.
        """
        key = str(name)
        try:
            period = max(0.001, float(every))
        except (TypeError, ValueError) as exc:
            note_failure("liveness:L150", exc)
            return False
        now = time.time()
        with self._lock:
            if len(self._required) >= _MAX_NAMES and key not in self._required:
                return False
            cur = self._required.get(key)
            if cur is None:
                self._required[key] = {"every": period, "since": now}
            else:
                cur["every"] = min(float(cur.get("every", period)), period)
            self._declared_ever.add(key)
            # A declaration alone is not a beat: the requirement is armed, and
            # the deadline runs from now.
            self._beats.setdefault(key, {"last": None, "count": 0})
            return True

    def expect(self, name: str, ttl: float = 60.0, detail: str = "") -> float:
        """Arm a one-shot expectation. Returns its deadline (0.0 on refusal).

        Re-arming the same name replaces the outstanding obligation with the
        new deadline; two outstanding copies of one obligation would make the
        report ambiguous, and the later arm is the one that is really pending.
        """
        key = str(name)
        try:
            ttl_f = max(0.001, float(ttl))
        except (TypeError, ValueError) as exc:
            note_failure("liveness:L177", exc)
            return 0.0
        now = time.time()
        with self._lock:
            if len(self._open) >= _MAX_NAMES and key not in self._open:
                return 0.0
            deadline = now + ttl_f
            self._open[key] = {"deadline": deadline, "armed_at": now,
                               "ttl": ttl_f, "detail": str(detail)[:120]}
            self._armed += 1
            self._armed_ever.add(key)
            return deadline

    # -- discharge -------------------------------------------------------
    def fulfill(self, name: str, detail: str = "") -> bool:
        """Discharge an outstanding one-shot. True when one was actually open.

        Returns False for an unknown or already-discharged name rather than
        raising: a double-fulfil is a wiring question, not a crash.
        """
        key = str(name)
        with self._lock:
            rec = self._open.pop(key, None)
            if rec is None:
                # Was it armed late (after the deadline already passed and it
                # was reported)? Then record the fulfilment as healing the miss
                # rather than silently dropping it.
                healed = 0
                for miss in self._history:
                    if miss.get("name") == key and not miss.get("healed_at"):
                        miss["healed_at"] = now_iso()
                        healed += 1
                return False if not healed else True
            self._fulfilled += 1
            rec["fulfilled_at"] = now_iso()
            if detail:
                rec["fulfil_detail"] = str(detail)[:120]
            return True

    def beat(self, name: str, every: float = 60.0, detail: str = "") -> bool:
        """Declare (if needed) and record a beat for a recurring obligation."""
        self.require(name, every)
        key = str(name)
        now = time.time()
        with self._lock:
            rec = self._beats.get(key)
            if rec is None:
                rec = {"last": None, "count": 0}
                self._beats[key] = rec
            rec["last"] = now
            rec["count"] = int(rec.get("count", 0)) + 1
            if detail:
                rec["detail"] = str(detail)[:120]
            self._beat_count += 1
            self._declared_ever.add(key)
            return True

    # -- reading ---------------------------------------------------------
    def overdue(self, grace: float = 1.0, now: Optional[float] = None) -> list:
        """Every obligation currently unmet. **Idempotent** -- it does not consume.

        ``grace`` scales the deadline rather than shifting it, so a caller can
        ask for "20% late" without knowing each obligation's period.

        Idempotence is not a detail, it is the contract a monitor needs. An
        earlier version marked a miss as reported and returned it only once, so
        a monitor polling every 60s saw the fault on one poll and a clean board
        on the next -- a diagnostic whose output decays is indistinguishable
        from a system that fixed itself. A miss stays in this list for as long
        as it is genuinely unmet, and leaves it only when it is discharged (or
        when a beat resumes).

        Latching still happens, but only as HISTORY: the first time a given
        (name, kind) is seen it is appended to the bounded miss log, so
        ``misses_total`` counts distinct misses rather than polls.
        """
        ts = time.time() if now is None else float(now)
        try:
            slack = max(1.0, float(grace))
        except (TypeError, ValueError) as exc:
            note_failure("liveness:L256", exc)
            slack = 1.0
        found: list[dict] = []
        with self._lock:
            for key, rec in list(self._open.items()):
                deadline = float(rec.get("deadline", 0.0))
                if ts > deadline:
                    found.append({
                        "name": key, "kind": "unfulfilled",
                        "overdue_by": round(ts - deadline, 3),
                        "detail": rec.get("detail", ""),
                    })
            for key, rec in self._beats.items():
                last = rec.get("last")
                req = self._required.get(key)
                if req is None:
                    continue
                period = float(req.get("every", 60.0)) * slack
                reference = float(last) if last is not None else float(
                    req.get("since", ts))
                if ts - reference > period:
                    found.append({
                        "name": key,
                        "kind": "silent" if last is not None else "never_beaten",
                        "overdue_by": round(ts - reference - period, 3),
                        "detail": rec.get("detail", ""),
                    })
            for miss in found:
                token = (miss["name"], miss["kind"])
                if token in self._seen:
                    continue
                self._seen.add(token)
                self._history.append(miss)
            if len(self._history) > _MAX_HISTORY:
                del self._history[:len(self._history) - _MAX_HISTORY]
        return found

    def unmonitored(self) -> list:
        """Names that were declared and are no longer armed by anything.

        A recurring obligation that has stopped being armed entirely is the
        failure an occurrence monitor cannot see and a heartbeat cannot either:
        a heartbeat that is never beaten looks exactly like a heartbeat that is
        being beaten on time. This names them.
        """
        with self._lock:
            return sorted(k for k in self._declared_ever
                          if k not in self._required and k not in self._open)

    def snapshot(self, grace: float = 1.0) -> dict:
        found = self.overdue(grace=grace)
        with self._lock:
            open_now = len(self._open)
            beats = len(self._beats)
            required = len(self._required)
            declared_ever = len(self._declared_ever)
            armed_ever = len(self._armed_ever)
            armed = self._armed
            fulfilled = self._fulfilled
            beat_total = self._beat_count
            history = [dict(m) for m in self._history]
            unmonitored = sorted(k for k in self._declared_ever
                                 if k not in self._required and k not in self._open)
            since = self._monitored_since
        return {
            "armed_now": open_now,
            "beating": beats,
            "required": required,
            "declared_ever": declared_ever,
            "armed_ever": armed_ever,
            "armed_total": armed,
            "fulfilled_total": fulfilled,
            "beats_total": beat_total,
            "overdue": found,
            "misses_total": len(history),
            "history": history[-20:],
            "unmonitored": unmonitored,
            # The distinction the whole module exists for: green with nothing
            # ever declared OR armed is UNKNOWN, not healthy. A one-shot counts
            # as being watched even though it never enters `_declared_ever`,
            # which is why this asks both ledgers.
            "healthy": not found,
            "observable": bool(self._declared_ever or self._armed_ever),
            "monitored_since": since,
        }

    def reset(self, clear_declarations: bool = False) -> int:
        """Drop obligation state. Declarations survive unless asked.

        Clearing declarations by default would make a reset look like "nothing
        is wrong" -- the exact ambiguity `observable` exists to remove -- so a
        plain reset keeps the promise and only forgets the progress.
        """
        with self._lock:
            n = len(self._open) + len(self._beats)
            self._open.clear()
            self._history.clear()
            self._seen.clear()
            self._armed = 0
            self._fulfilled = 0
            self._beat_count = 0
            self._monitored_since = time.time()
            if clear_declarations:
                self._required.clear()
                self._beats.clear()
                self._declared_ever.clear()
                self._armed_ever.clear()
            else:
                for rec in self._beats.values():
                    rec["last"] = None
                    rec["count"] = 0
                now = time.time()
                for rec in self._required.values():
                    rec["since"] = now
            return n


LEDGER = LivenessLedger()


def now_iso() -> str:
    """Local ISO timestamp. Kept here so the ledger owns its own clock reads."""
    try:
        from datetime import datetime
        return datetime.now().isoformat()
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("liveness:L381", exc)
        return ""


# -- module-level proxies ------------------------------------------------
# Every one of these is wrapped: the mesh must keep running while it is being
# measured, so a misbehaving ledger degrades to a no-op instead of an exception
# on a hot path.

def require(name: str, every: float = 60.0) -> bool:
    """Declare a recurring obligation. See ``LivenessLedger.require``."""
    try:
        return LEDGER.require(name, every)
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("liveness:L394", exc)
        return False


def expect(name: str, ttl: float = 60.0, detail: str = "") -> float:
    """Arm a one-shot obligation. See ``LivenessLedger.expect``."""
    try:
        return LEDGER.expect(name, ttl, detail)
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("liveness:L402", exc)
        return 0.0


def fulfill(name: str, detail: str = "") -> bool:
    """Discharge a one-shot obligation. See ``LivenessLedger.fulfill``."""
    try:
        return LEDGER.fulfill(name, detail)
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("liveness:L410", exc)
        return False


def beat(name: str, every: float = 60.0, detail: str = "") -> bool:
    """Record a beat for a recurring obligation."""
    try:
        return LEDGER.beat(name, every, detail)
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("liveness:L418", exc)
        return False


class _Obliged:
    """Context manager: arms a one-shot on entry, discharges it on success.

    The useful failure is the one where the block never finishes. An exception
    leaves the obligation armed, so the miss is reported with the deadline
    rather than needing the caller to remember to arm it in a `finally`.
    """

    __slots__ = ("name", "ttl", "detail", "armed", "entered")

    def __init__(self, name: str, ttl: float, detail: str) -> None:
        self.name = str(name)
        self.ttl = float(ttl)
        self.detail = str(detail)
        self.armed = False
        self.entered = False

    def __enter__(self) -> "_Obliged":
        if expect(self.name, self.ttl, self.detail):
            self.armed = True
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self.armed and exc_type is None:
            fulfill(self.name)
        return False


def obliged(name: str, ttl: float = 60.0, detail: str = "") -> _Obliged:
    """``with obliged("x", ttl=5): ...`` — see ``_Obliged``."""
    return _Obliged(name, ttl, detail)


def overdue(grace: float = 1.0) -> list:
    """Currently unmet obligations. See ``LivenessLedger.overdue``."""
    try:
        return LEDGER.overdue(grace=grace)
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("liveness:L460", exc)
        return []


def unmonitored() -> list:
    """Declared-but-no-longer-armed names. See ``LivenessLedger.unmonitored``."""
    try:
        return LEDGER.unmonitored()
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("liveness:L468", exc)
        return []


def liveness_report(grace: float = 1.0) -> dict:
    """Full snapshot for the monitors. Never raises."""
    try:
        return LEDGER.snapshot(grace=grace)
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("liveness:L476", exc)
        return {
            "armed_now": 0, "beating": 0, "required": 0, "declared_ever": 0,
            "armed_ever": 0, "armed_total": 0, "fulfilled_total": 0,
            "beats_total": 0, "overdue": [], "misses_total": 0, "history": [],
            "unmonitored": [], "healthy": False, "observable": False,
            "monitored_since": 0.0,
        }


def liveness_reset(clear_declarations: bool = False) -> int:
    """Drop obligation state. For tests that assert on absolute counts."""
    try:
        return LEDGER.reset(clear_declarations=clear_declarations)
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("liveness:L490", exc)
        return 0
