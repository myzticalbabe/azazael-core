# coding: utf-8
"""Hybrid logical clock (HLC) — causally-safe ordering for cross-env merges.

Deep dive 2026-09-15. The mesh's cross-env agreement layer is last-writer-wins,
and "last" was decided by comparing ``datetime.now().isoformat()`` strings.
Three things were wrong with that, and all three are silent:

1. **No timezone.** ``_now_iso()`` is naive local time. An env on UTC-8 and an
   env on UTC+0 emit strings that *look* comparable and are eight hours apart,
   so the newer write loses — for eight hours. Nothing detects it; the merge
   just picks the "wrong" side and reports success.
2. **Clock trust.** A peer whose clock runs fast wins every tie, forever, and a
   peer whose clock is behind has its writes discarded indefinitely. There is
   no clock-agreement assumption a distributed system should be making without
   at least *measuring* it.
3. **No causality.** String compare cannot express "this write happened after
   the write I just observed", so a value derived from a peer's state is not
   ordered after that state.

An HLC (Kulkarni et al., 2014) keeps the causal monotonicity of a Lamport
clock while staying close to physical time. It is what CockroachDB, MongoDB
and Azure Cosmos DB use for exactly this job. Properties this gives the mesh:

* **Monotone**, including when the wall clock jumps backwards (NTP step, DST,
  suspend/resume) — the clock never returns a stamp it has already returned.
* **Causal**: after ``observe(remote)``, every subsequent local stamp is
  strictly greater than the observed one.
* **Deterministic tie-break** by node id, so two envs stamping the same
  millisecond still converge on the same winner without coordination.
* **Comparable as a string.** The packed form is fixed-width and
  lexicographically ordered, so it drops into the existing string comparisons
  and JSON persistence with no format migration.

It is stdlib-only, so ``CORE.spine_registry`` keeps its no-heavy-imports rule.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Optional, Tuple, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = [
    "HybridLogicalClock", "hlc", "pack_stamp", "unpack_stamp",
    "normalize_stamp", "PHYSICAL_DIGITS", "COUNTER_DIGITS", "MAX_DRIFT_MS",
    "MAX_PHYSICAL",
]

# 15 digits of milliseconds covers year 33658; 5 digits of counter is far more
# than any burst before the next physical tick. Fixed width is what makes the
# packed stamp lexicographically sortable.
PHYSICAL_DIGITS = 15
COUNTER_DIGITS = 5
MAX_COUNTER = 10 ** COUNTER_DIGITS - 1
# The packed form is a FIXED-width field, so a physical value wider than the
# field produces a stamp `unpack_stamp()` cannot read — a value that is neither
# ordered nor recognised, i.e. silently absent from the ordering it was created
# for. `pack_stamp` therefore clamps, which keeps the invariant that *every*
# stamp this module hands out is unpackable. Clamping is the right failure
# direction: the alternative (printing 16 digits) yields a stamp that is
# unreadable rather than merely imprecise.
MAX_PHYSICAL = 10 ** PHYSICAL_DIGITS - 1

# How far a REMOTE stamp may pull our clock forward. Without a bound, a peer
# with a badly-set clock (or a hostile payload) pins every future stamp on
# this host to that peer's era: our writes then look newer than everything
# for years, and a correct peer's later write loses to them forever. The
# clock stays correct locally but becomes useless for ordering against
# everyone else. CockroachDB ships the same knob under the name MaxOffset.
#
# Exceeding it does not lose the observation — `skew_ms()` still reports it —
# it only stops the remote time from being adopted. Ordering of the peer's
# *values* is unaffected (that uses the peer's stamp directly); this only
# protects the local clock.
MAX_DRIFT_MS = 300_000

_LEGACY_NODE = "legacy"

_STAMP_RE = re.compile(
    r"^(\d{%d})-(\d{%d})-(.*)$" % (PHYSICAL_DIGITS, COUNTER_DIGITS)
)


def _node_id() -> str:
    """Node identity for tie-breaking — the canonical env id."""
    try:
        from CORE.env_id import env_id
        return str(env_id())[:24] or "unknown"
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("hlc:L90", exc)
        return "unknown"


def pack_stamp(physical_ms: int, counter: int, node: str = "") -> str:
    """Pack an HLC triple into a fixed-width, lexicographically ordered string.

    Both fields are clamped to their width, so the result is always a stamp
    ``unpack_stamp()`` can read (see ``MAX_PHYSICAL``).
    """
    try:
        physical = int(physical_ms)
    except Exception as exc:
        note_failure("hlc:L102", exc)
        physical = 0
    return "%0*d-%0*d-%s" % (
        PHYSICAL_DIGITS, max(0, min(MAX_PHYSICAL, physical)),
        COUNTER_DIGITS, max(0, min(MAX_COUNTER, int(counter))),
        str(node or "")[:24],
    )


def unpack_stamp(stamp: str) -> Optional[Tuple[int, int, str]]:
    """Unpack an HLC stamp. Returns None for anything that is not one.

    ``None`` is the important case: it is how a legacy naive-ISO timestamp is
    recognised, so callers can normalise rather than silently mis-order.
    """
    if not isinstance(stamp, str):
        return None
    m = _STAMP_RE.match(stamp)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), m.group(3)


def normalize_stamp(value: str, node: str = _LEGACY_NODE) -> str:
    """Coerce any timestamp into an HLC stamp. Never raises.

    Already-packed HLC stamps pass through untouched (the migration must be
    idempotent). A legacy naive-ISO or epoch value is converted to a stamp so
    it participates in the same ordering as new writes.

    A legacy naive timestamp is interpreted in *local* time, which is exactly
    the ambiguity being removed — so the converted value can be off by the
    writer's UTC offset. That is still strictly better than the old behaviour,
    which compared the same ambiguous strings forever; the conversion makes the
    ambiguity bounded (one window at upgrade time) instead of permanent.
    """
    if isinstance(value, str):
        if unpack_stamp(value) is not None:
            return value
        text = value.strip()
        if text:
            try:
                from datetime import datetime
                return pack_stamp(int(datetime.fromisoformat(text).timestamp() * 1000),
                                  0, node)
            except Exception as exc:
                note_failure("hlc:L147", exc)
            try:
                magnitude = abs(float(text))
                # Unit detection by MAGNITUDE. Note the old code multiplied by
                # 1000 unconditionally ("seconds"), which is exactly the kind of
                # ambiguity this module exists to remove, and which fails
                # loudly rather than quietly:
                #   * a millisecond epoch, "1789493268276", became a 16-digit
                #     value that `unpack_stamp()` cannot parse at all, and
                #   * a bare "32472162000" became year 2999 — which then
                #     propagated into every persisted stamp on the host.
                if magnitude >= 1e17:        # nanoseconds
                    millis = magnitude / 1e6
                elif magnitude >= 1e14:      # microseconds
                    millis = magnitude / 1e3
                elif magnitude >= 1e11:      # milliseconds (sat near here)
                    millis = magnitude
                else:                        # seconds
                    millis = magnitude * 1000
                return pack_stamp(int(millis), 0, node)
            except Exception as exc:
                note_failure("hlc:L168", exc)
    return pack_stamp(0, 0, node)


class HybridLogicalClock:
    """Thread-safe hybrid logical clock. Stdlib only.

    One instance per process is enough (see the module-level ``hlc()``), and
    the mesh shares it across every fabric store so that all stamps come from
    a single monotone sequence — a per-store clock could hand out a lower
    stamp after a higher one, which is precisely the inversion being fixed.
    """

    def __init__(self, node: Optional[str] = None):
        self._node = str(node or _node_id())[:24] or "unknown"
        self._lock = threading.Lock()
        self._physical = 0
        self._counter = 0
        # Observability: the newest physical time any TRUSTED peer has shown
        # us. The mesh has never measured clock skew; without it, divergence
        # caused by skew is indistinguishable from a genuine disagreement.
        self._max_remote_physical = 0
        # Kept separate from the above on purpose (see `observe`): a stamp we
        # REFUSED must not be reported as "how far ahead our peers are", or a
        # single poisoned value turns the diagnostic into a permanent lie.
        self._max_rejected_physical = 0
        self._observations = 0
        self._rejected = 0

    # -- introspection -------------------------------------------------

    @property
    def node(self) -> str:
        return self._node

    def peek(self) -> str:
        """Current stamp without advancing the clock."""
        with self._lock:
            return pack_stamp(self._physical, self._counter, self._node)

    def skew_ms(self) -> int:
        """How far ahead of us the newest *accepted* peer clock is.

        ``> 0`` means a peer's clock is ahead of ours. Purely diagnostic — it
        never influences ordering, which is the point of using an HLC.

        Deliberately excludes stamps that were refused for exceeding
        ``MAX_DRIFT_MS``: those are counted by ``rejected()`` and measured by
        ``rejected_skew_ms()`` instead. Reporting a refused stamp here made
        this number meaningless — one legacy value from a broken peer pinned it
        to a figure like +30,682,668,693,692ms (~972 years) forever, which is
        indistinguishable from a real reading.
        """
        with self._lock:
            if not self._max_remote_physical:
                return 0
            return int(self._max_remote_physical - (time.time() * 1000))

    def rejected_skew_ms(self) -> int:
        """How far ahead the worst REFUSED stamp was (0 if none refused).

        This is the "a peer's clock is broken" signal, kept apart from
        ``skew_ms()``'s "a peer's clock runs a little fast" signal so neither
        number can be mistaken for the other.
        """
        with self._lock:
            if not self._max_rejected_physical:
                return 0
            return int(self._max_rejected_physical - (time.time() * 1000))

    def observations(self) -> int:
        with self._lock:
            return self._observations

    def rejected(self) -> int:
        """Count of remote stamps outside the drift window (not adopted)."""
        with self._lock:
            return self._rejected

    def reset_remote_view(self) -> None:
        """Clear only the *observability* fields.

        Deliberately does NOT rewind `_physical`/`_counter`: a logical clock
        that goes backwards is exactly the failure mode this module exists to
        prevent, so a reset must never un-monotone it.

        This is also the operator escape hatch for a store that already holds
        stamps from a poisoned era: refusal stops them being adopted, but they
        would otherwise sit in the diagnostics forever.
        """
        with self._lock:
            self._max_remote_physical = 0
            self._max_rejected_physical = 0
            self._observations = 0
            self._rejected = 0

    # -- core ----------------------------------------------------------

    def stamp(self) -> str:
        """Advance and return a new stamp (strictly greater than the last)."""
        with self._lock:
            return self._advance_locked()

    def _advance_locked(self) -> str:
        now = int(time.time() * 1000)
        if now > self._physical:
            self._physical = now
            self._counter = 0
        else:
            # Same millisecond (or the wall clock went BACKWARDS): keep the
            # logical component moving forward. This is the property a bare
            # wall clock does not have, and why an NTP step cannot invert
            # ordering here.
            self._counter += 1
            if self._counter > MAX_COUNTER:
                self._physical += 1
                self._counter = 0
        return pack_stamp(self._physical, self._counter, self._node)

    def observe(self, remote: str, max_drift_ms: Optional[int] = None) -> str:
        """Merge a remote stamp, then return a stamp strictly greater than it.

        Textbook HLC merge: take the max of (local, physical now, remote), and
        pick the counter from the branch that produced the max, incrementing so
        the result is a *new* event ordered after everything seen.

        A remote physical time beyond ``max_drift_ms`` (default
        ``MAX_DRIFT_MS``) is observed and reported but NOT adopted, so one
        broken clock cannot pin this host's clock to it. The returned stamp is
        then not causally after such a remote — a deliberate trade, since
        jumping the local clock by years to satisfy a bad peer is strictly
        worse than refusing to. ``rejected()`` counts these.
        """
        limit = MAX_DRIFT_MS if max_drift_ms is None else int(max_drift_ms)
        with self._lock:
            unpacked = unpack_stamp(remote)
            remote_physical = unpacked[0] if unpacked else 0
            remote_counter = unpacked[1] if unpacked else 0
            if unpacked is None and remote:
                # A legacy timestamp we merely want to stay causally after.
                normalized = unpack_stamp(normalize_stamp(remote))
                if normalized:
                    remote_physical, remote_counter = normalized[0], normalized[1]
            self._observations += 1

            now = int(time.time() * 1000)
            if remote_physical > now + limit:
                # Do not let the remote era become ours.
                self._rejected += 1
                # Record the refusal in its OWN field. Updating
                # `_max_remote_physical` before this guard (the original
                # order) meant a refused stamp still became "the newest peer
                # clock", so `skew_ms()` reported the rejected era — a live
                # store with 98 year-2999 stamps reported +30,682,668,693,692ms
                # (≈972 years) as clock skew, forever, with no way to tell it
                # apart from a real measurement.
                if remote_physical > self._max_rejected_physical:
                    self._max_rejected_physical = remote_physical
                remote_physical = now
                remote_counter = 0
            elif remote_physical > self._max_remote_physical:
                self._max_remote_physical = remote_physical

            newest = max(self._physical, now, remote_physical)
            if newest == self._physical == remote_physical:
                self._counter = max(self._counter, remote_counter) + 1
            elif newest == self._physical:
                self._counter += 1
            elif newest == remote_physical:
                self._counter = remote_counter + 1
            else:
                self._counter = 0
            if self._counter > MAX_COUNTER:
                newest += 1
                self._counter = 0
            self._physical = newest
            return pack_stamp(self._physical, self._counter, self._node)


_HLC_LOCK = threading.Lock()
_HLC: Optional[HybridLogicalClock] = None


def hlc() -> HybridLogicalClock:
    """Process-wide clock. One sequence for every store that needs ordering."""
    global _HLC
    if _HLC is None:
        with _HLC_LOCK:
            if _HLC is None:
                _HLC = HybridLogicalClock()
    return _HLC
