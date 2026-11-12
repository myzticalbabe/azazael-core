# coding: utf-8
"""Silent-failure ledger — swallowed exceptions as a trended metric.

Why this module exists
----------------------
The 2026-09-16 per-function pre-flight audit ran probe PF15 over the mesh and
found 118 handlers whose entire body is ``pass``. Read one at a time, almost
all of them are defensible: a heartbeat that cannot reach a peer, a monitor
whose optional dependency is absent, a cache write that lost a race. Each says
"this failure does not matter right now", and each is right.

Read together, they describe a property the mesh cannot currently detect. When
``load_spine_state`` (audit probe PF14 + PF15 + PF18, complexity 61) swallows
an exception, two very different worlds produce byte-identical behaviour:

* the mesh degraded gracefully and healed on the next cycle, and
* the mesh has been failing to load its own learning on every start for a week.

That second case is not hypothetical -- this session already found tests writing
the live spine store, and a stale store silently serving synthetic fixtures. It
was found by a human reading a file, not by the system noticing.

The insight
-----------
A swallowed exception is not a control-flow decision, it is a *measurement*, and
the measurement is currently thrown away. ``pass`` destroys the only evidence
that the fabric was ever under stress. If the mesh counts what it chooses to
ignore, then "the fabric healed" becomes a claim the monitors can check, because
an unchanged count is not the same as a count nobody keeps.

Two things make this more than a counter:

*Signature clustering.* A tag whose count rises is usually fine (a flaky peer).
A tag that grows a *new* exception type is not: that is a failure mode nobody
predicted, wearing the costume of the one they did. So the ledger remembers the
distinct ``type(message)`` signatures per tag, and a rising signature count is
the alarm -- not a rising total.

*Budgets.* A tag declares how often it is *allowed* to absorb a failure. Past
budget, the swallow is no longer graceful degradation; it is a fault being
hidden, and ``tripped()`` says so.

Never raises. Never logs by default. A ledger that can itself fail is worse than
no ledger, and the mesh must keep running while it is being measured.

Usage::

    from CORE.WORMS.expansion.silence import ignoring

    def load_spine_state():
        with ignoring("spine.load", budget=0):
            ...                                 # a failure here is a fault
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
    "ignoring", "record", "silence_report", "silence_reset",
    "tripped", "signatures", "SilenceLedger",
]

# Distinct signatures kept per tag. Bounded: a damaged loop can raise a
# different message every iteration, and an unbounded ledger is a leak.
_MAX_SIGNATURES = 24

# Distinct tags kept. The mesh has 1410 functions; more tags than this means a
# tag is being built from data rather than from a literal.
_MAX_TAGS = 512


class SilenceLedger:
    """Per-tag counts, distinct failure signatures, and declared budgets."""

    __slots__ = ("_lock", "_counts", "_budgets", "_sigs", "_first", "_last")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}
        self._budgets: dict[str, int] = {}
        self._sigs: dict[str, dict[str, int]] = {}
        self._first: dict[str, float] = {}
        self._last: dict[str, float] = {}

    def declare(self, tag: str, budget: int) -> None:
        """State how many failures this tag is allowed to absorb silently."""
        with self._lock:
            self._budgets[str(tag)] = int(budget)

    def note(self, tag: str, exc: BaseException) -> int:
        """Record one ignored failure. Returns the new total for the tag."""
        key = str(tag)
        sig = "%s: %s" % (type(exc).__name__, str(exc)[:120])
        now = time.time()
        with self._lock:
            total = self._counts.get(key, 0) + 1
            self._counts[key] = total
            self._first.setdefault(key, now)
            self._last[key] = now
            bucket = self._sigs.setdefault(key, {})
            if sig in bucket:
                bucket[sig] += 1
            elif len(bucket) < _MAX_SIGNATURES:
                bucket[sig] = 1
            elif len(self._counts) > _MAX_TAGS:
                # Evict the least recently used tag rather than the newest
                # signature: a fresh signature is the interesting one.
                oldest = min(self._last, key=lambda k: self._last[k])
                self._counts.pop(oldest, None)
                self._sigs.pop(oldest, None)
                self._budgets.pop(oldest, None)
                self._first.pop(oldest, None)
                self._last.pop(oldest, None)
            return total

    def count(self, tag: str) -> int:
        with self._lock:
            return self._counts.get(str(tag), 0)

    def tripped(self, tag: Optional[str] = None) -> Any:
        """Tag(s) that have absorbed more failures than they declared.

        Returns a bool for one tag, or the list of tripped tags for all.
        """
        with self._lock:
            if tag is not None:
                key = str(tag)
                return self._counts.get(key, 0) > self._budgets.get(key, 0)
            return sorted(
                k for k, v in self._counts.items()
                if v > self._budgets.get(k, 0)
            )

    def signatures(self, tag: str) -> dict[str, int]:
        with self._lock:
            return dict(self._sigs.get(str(tag), {}))

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "tags": len(self._counts),
                "total_ignored": sum(self._counts.values()),
                "tripped": sorted(
                    k for k, v in self._counts.items()
                    if v > self._budgets.get(k, 0)
                ),
                "counts": dict(self._counts),
                "signatures": {k: len(v) for k, v in self._sigs.items()},
                "first_seen": dict(self._first),
                "last_seen": dict(self._last),
            }

    def reset(self) -> int:
        with self._lock:
            n = len(self._counts)
            self._counts.clear()
            self._sigs.clear()
            self._first.clear()
            self._last.clear()
            return n


LEDGER = SilenceLedger()


class _Ignoring:
    """Context manager handed out by ``ignoring()``."""

    __slots__ = ("_tag", "_budget", "_reraise", "_exception")

    def __init__(self, tag: str, budget: int, reraise: bool) -> None:
        self._tag = tag
        self._budget = budget
        self._reraise = reraise
        self._exception: Optional[BaseException] = None

    @property
    def exception(self) -> Optional[BaseException]:
        """The failure that was absorbed, if any. Inspectable, never fatal."""
        return self._exception

    def __enter__(self) -> "_Ignoring":
        LEDGER.declare(self._tag, self._budget)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is None:
            return False
        self._exception = exc
        LEDGER.note(self._tag, exc)
        if self._reraise:
            return False
        return True


def ignoring(tag: str, budget: int = 0, reraise: bool = False) -> _Ignoring:
    """Absorb failures under ``tag``, and account for them.

    ``budget`` is how many failures this tag may absorb before ``tripped()``
    reports it. The default of 0 means "this failure is a fault": the call still
    does not raise (the mesh must survive), but the ledger says so immediately,
    which is the property a bare ``pass`` cannot express.
    """
    return _Ignoring(str(tag), int(budget), bool(reraise))


def record(tag: str, exc: BaseException) -> int:
    """Account for a failure that was absorbed by hand-written code."""
    try:
        return LEDGER.note(str(tag), exc)
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("silence:L214", exc)
        return 0


def tripped(tag: Optional[str] = None) -> Any:
    """Proxy for ``LEDGER.tripped``."""
    try:
        return LEDGER.tripped(tag)
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("silence:L222", exc)
        return False if tag is not None else []


def signatures(tag: str) -> dict[str, int]:
    """Distinct failure signatures absorbed under ``tag``."""
    try:
        return LEDGER.signatures(tag)
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("silence:L230", exc)
        return {}


def silence_report() -> dict:
    """Full ledger snapshot. Never raises."""
    try:
        return LEDGER.snapshot()
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("silence:L238", exc)
        return {"tags": 0, "total_ignored": 0, "tripped": [], "counts": {},
                "signatures": {}, "first_seen": {}, "last_seen": {}}


def silence_reset() -> int:
    """Clear the ledger. For tests that assert on absolute counts."""
    try:
        return LEDGER.reset()
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("silence:L247", exc)
        return 0
