# coding: utf-8
"""Fencing tokens for shared mesh state — stale writers rejected by construction.

Why this module exists
----------------------
The 2026-09-16 per-function pre-flight audit found ``EvolutionEngine.evolve``
snapshotting ``self._population`` under a lock, doing a whole generation of
crossover and mutation *outside* it, and then writing back under the lock
without ever asking whether another thread had already finished a generation in
the meantime. Two concurrent callers therefore both snapshotted the same
population, both wrote, and one round of offspring vanished while
``self._generation`` advanced twice. The fix at the call site is three lines:
capture the generation, re-check it before writing, discard the round if it
changed.

That is the right fix for that function. But it is not a *general* fix, because
it is a check-then-act guard, and a check-then-act guard only works if every
author who later touches the code remembers to write the check. Fifteen
functions in the mesh write module-level state (audit probe PF18) and the
read-modify-write shape recurs across the fabric, the spine and the monitors.

The insight
-----------
Kleppmann's fencing tokens, applied to in-process shared state rather than to
distributed locks. A writer does not ask "is it still safe for me to write?" --
it presents a *token* that was minted before it began, and the commit path is
the only thing that writes. A stale token cannot be committed, so the stale
write is impossible rather than merely discouraged, and it is impossible
without the author of the next function having to know anything about it.

The token does not need to be a clock or a lock and does not need to be
compared to anything the writer believes. Monotonicity plus a single commit
point is the whole mechanism.

Deliberate limits
-----------------
- In-process only. Cross-environment writes need a token that survives the
  hop, which is the HighLevelClock's job; this is the single-process half.
- ``claim()`` does not block. A fence is a rejection mechanism, not a mutex,
  so contention is resolved by one writer winning and the rest being told.
- Never raises. The mesh must keep running even if a fence misbehaves.

Usage::

    from CORE.WORMS.expansion.fencing import fence_for

    def evolve(self):
        f = fence_for("evolution.population")
        token = f.claim()
        ...                                     # expensive work, unlocked
        ok, result = f.commit(token, lambda: self._publish(new_population))
        if not ok:
            return {"error": "superseded", "generation": self._generation}
"""

from __future__ import annotations

import threading
from typing import Any, Callable, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

__all__ = ["Fence", "fence_for", "fences_reset", "fence_report"]


class Fence:
    """Monotonic token mint for one shared resource.

    Tokens increase by one per ``claim()``. Holding a token proves only that
    you started before someone else claimed a higher one; it proves nothing
    about how long you took, which is exactly why it is safe to compare.
    """

    __slots__ = ("_name", "_lock", "_token", "_claims", "_rejections", "_commits")

    def __init__(self, name: str) -> None:
        self._name = str(name)
        self._lock = threading.Lock()
        self._token = 0
        self._claims = 0
        self._rejections = 0
        self._commits = 0

    # -- minting ---------------------------------------------------------
    def claim(self) -> int:
        """Mint and return the next token. Always succeeds, never blocks."""
        with self._lock:
            self._token += 1
            self._claims += 1
            return self._token

    def token(self) -> int:
        """The current (highest minted) token."""
        with self._lock:
            return self._token

    def is_current(self, token: int) -> bool:
        """True when nothing has claimed since ``token`` was minted."""
        try:
            return int(token) == self.token()
        except Exception as exc:
            note_failure("fencing:L99", exc)
            return False

    # -- committing ------------------------------------------------------
    def commit(self, token: int, apply: Callable[[], Any]) -> tuple[bool, Any]:
        """Run ``apply`` and write, only if ``token`` is still the newest.

        Returns ``(committed, value)``. The check and the call happen under the
        fence's lock, so between them no other claim can land: that is the
        property that makes the stale write impossible rather than unlikely.

        A stale token is counted and skipped -- never raised, never applied.
        """
        with self._lock:
            if int(token) != self._token:
                self._rejections += 1
                return False, None
            try:
                value = apply()
            except Exception as exc:  # a failed apply must not burn the token
                note_failure("fencing:L118", exc)
                return False, exc
            self._commits += 1
            return True, value

    # -- reporting -------------------------------------------------------
    def snapshot(self) -> dict:
        """Counters, for the monitors and the immune system to trend."""
        with self._lock:
            return {
                "name": self._name,
                "token": self._token,
                "claims": self._claims,
                "commits": self._commits,
                "rejections": self._rejections,
            }

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        s = self.snapshot()
        return (f"Fence({s['name']!r}, token={s['token']}, "
                f"rejections={s['rejections']})")


_FENCES: dict[str, Fence] = {}
_FENCES_LOCK = threading.Lock()


def fence_for(name: str) -> Fence:
    """Return the process-wide Fence for ``name``, creating it on first use.

    Process-wide on purpose: the bug class this exists to prevent is two
    *instances* or two threads racing over one resource, so the fence has to
    span the instances. Never raises.
    """
    key = str(name)
    try:
        with _FENCES_LOCK:
            f = _FENCES.get(key)
            if f is None:
                f = Fence(key)
                _FENCES[key] = f
            return f
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("fencing:L160", exc)
        return Fence(key)


def fences_reset() -> int:
    """Drop every fence. For tests that need a clean token space."""
    try:
        with _FENCES_LOCK:
            n = len(_FENCES)
            _FENCES.clear()
            return n
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("fencing:L171", exc)
        return 0


def fence_report() -> dict:
    """Every live fence's counters, keyed by name. Never raises."""
    try:
        with _FENCES_LOCK:
            return {k: f.snapshot() for k, f in _FENCES.items()}
    except Exception as exc:  # pragma: no cover - defensive
        note_failure("fencing:L180", exc)
        return {}

