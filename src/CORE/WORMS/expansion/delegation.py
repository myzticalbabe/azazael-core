# coding: utf-8
"""
ECOSYSTEM EXPANSION — ask_owner mid-flight clarification channel.

A worm executing on a pool worker thread sometimes cannot proceed without
input from its owner (ambiguous target, missing credential, destructive
action needing confirmation). Before this module the only options were to
fail or guess — both recorded as ordinary failures, indistinguishable from
real defects.

The channel is deliberately narrow:

* Worm code raises ``NeedsClarification(question)`` or calls ``ask_owner()``
  directly. Both park the worker on a ``threading.Event`` with a deadline —
  never on stdin/UI, never unbounded (the pool watchdog kills hung waits).
* The owner (or an owner-side automation) answers via ``answer_question()``.
* The rendezvous is an in-process registry keyed by ``decision_id``. The
  ``MemoryBus`` is used as an observability tap only (``worm.question.>`` /
  ``worm.answer.>`` announcements, best-effort): question delivery never
  depends on bus subscription timing, so the channel works in tests and in
  processes without a bus.
* The executor boundary (``CLUSTERWORMS.WormExecutor._execute_direct``)
  normalises both outcomes to the standard result shape with
  ``needs_input`` set, so telemetry, breaker accounting and aggregation
  keep working. A clarification is NOT a transient failure: it must not
  trigger breaker retry, and it records at ``info``-adjacent severity
  discipline (success=False, error names the question, not a crash).

Fail-closed: timeout (default 30s, must stay below the pool watchdog) and
a missing bus both produce a ``needs_input`` outcome naming the question,
never a hang and never a guessed answer.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional, Tuple, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

#: Upper bound for a parked wait. Must stay below the pool watchdog /
#: ``as_completed`` timeouts that supervise worker threads, or the park
#: becomes the hang it exists to prevent.
DEFAULT_TIMEOUT_S = 30.0

#: Longest question text carried. Bounded like the telemetry error cap: a
#: question is a signature for the owner, not a transcript dump.
MAX_QUESTION_CHARS = 800

QUESTION_TOPIC = "worm.question"
ANSWER_TOPIC = "worm.answer"


class NeedsClarification(Exception):
    """Raised by worm code that needs owner input to proceed.

    Carries everything the executor needs to normalise the outcome without
    importing this module's internals.
    """

    def __init__(self, question: str, *,
                 decision_id: Optional[str] = None,
                 worm: Optional[str] = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S):
        super().__init__(str(question)[:MAX_QUESTION_CHARS])
        self.question = str(question)[:MAX_QUESTION_CHARS]
        self.decision_id = decision_id
        self.worm = worm
        self.timeout_s = float(timeout_s)


class ClarificationTimeout(Exception):
    """No answer arrived before the deadline. Fail-closed, never a guess."""

    def __init__(self, question: str, *,
                 decision_id: Optional[str] = None,
                 worm: Optional[str] = None,
                 waited_s: float = 0.0):
        super().__init__("clarification timed out: %s"
                         % str(question)[:MAX_QUESTION_CHARS])
        self.question = str(question)[:MAX_QUESTION_CHARS]
        self.decision_id = decision_id
        self.worm = worm
        self.waited_s = float(waited_s)


_lock = threading.Lock()
#: decision_id -> [threading.Event, answer slot]. In-process only: the wait
#: must not depend on bus delivery timing (tests, bus-less processes).
_waiters: Dict[str, list] = {}


def _new_decision_id() -> str:
    try:
        from CORE.WORMS.expansion.spawn_credit import new_decision_id
        return str(new_decision_id())
    except Exception as exc:
        note_failure("delegation:L97", exc)
        try:
            from CORE.uuid7 import uuid7
            return "d-local-%s" % uuid7().hex[:12]
        except Exception as exc:
            note_failure("delegation:L101", exc)
            return "d-local-%s" % uuid.uuid4().hex[:12]


def _announce(topic: str, payload: Dict[str, Any], bus: Any = None) -> None:
    """Best-effort observability tap. Never raises, never blocks."""
    try:
        if bus is None:
            try:
                from CORE.MemoryBus import MemoryBus
                bus = MemoryBus()
            except Exception as exc:
                note_failure("delegation:L112", exc)
                return
        bus.publish(topic, payload, source="delegation")
    except Exception as exc:
        note_failure("delegation:L115", exc)
    try:
        from CORE.local_memory import get_memory_store
        store = get_memory_store()
        if store is not None:
            store.record_event(
                source="delegation", event_type="worm_question",
                data={"topic": topic,
                      "question": str(payload.get("question", ""))[:300],
                      "decision_id": payload.get("decision_id"),
                      "worm": payload.get("worm"),
                      "answered": bool(payload.get("answered", False))},
                severity="info", tags=["delegation", "ask-owner"])
    except Exception as exc:
        note_failure("delegation:L129", exc)


def _person_context() -> Dict[str, Any]:
    """Best-effort person summary for the question announcement.

    Loads the fitted theta snapshot (`PSYCHE.traits.default_theta_path`,
    overridable via PSYCHE_THETA) and returns a small summary dict for the
    owner-side consumer to tailor wording or auto-answer low-risk asks.
    Returns {} when no snapshot exists or it is unusable — an absent person
    record is the NORMAL state, not a failure, so only genuine read/parse
    errors are recorded, never a missing file. Never raises.
    """
    try:
        from PSYCHE.traits import DOMAINS, default_theta_path
    except Exception as exc:
        note_failure("delegation:person-import", exc)
        return {}
    try:
        import json as _json
        text = default_theta_path().read_text(encoding="utf-8")
        payload = _json.loads(text)
    except OSError:
        return {}  # no snapshot yet; normal, not recorded
    except Exception as exc:
        note_failure("delegation:person-parse", exc)
        return {}
    try:
        raw_scores = payload.get("domain_scores", {}) or {}
        scores = {d: round(float(raw_scores[d]), 3) for d in DOMAINS
                  if isinstance(raw_scores.get(d), (int, float))
                  and 1.0 <= float(raw_scores[d]) <= 5.0}
        if not scores:
            return {}
        out: Dict[str, Any] = {"theta": scores}
        if payload.get("name"):
            out["name"] = str(payload["name"])[:80]
        if payload.get("source"):
            out["source"] = str(payload["source"])[:80]
        return out
    except Exception as exc:
        note_failure("delegation:person-shape", exc)
        return {}


def _chain_emit_allowed() -> bool:
    """Whether a behavior-chain row may be deposited now.

    Production-log quarantine (cf. conftest's AZAZAEL_CYCLE_LOG): test runs
    must not deposit chains the fitter would later fit as owner behavior.
    Skipped under pytest unless PSYCHE_CHAIN_TEST=1 forcibly enables the
    path for the hermetic tests (which use a fake store, never the live one).
    """
    if os.environ.get("PSYCHE_CHAIN_TEST") == "1":
        return True
    return not os.environ.get("PYTEST_CURRENT_TEST")


def _chain_record(actor: str, kind: str, action: str, planned: bool,
                  decision_id: Any = None, worm: Any = None) -> None:
    """Deposit one behavior-chain row for the PSYCHE fit loop (L4 producer).

    Actors: the asking worm (`persist`/`defer`, planned) at park, the owner
    (`fair`/`share`, planned) on answer, the owner (`fair`/`withhold`,
    unplanned) on waited timeout. Best-effort: never raises, never blocks,
    never fires under pytest (see _chain_emit_allowed).
    """
    if not _chain_emit_allowed():
        return
    try:
        from CORE.local_memory import get_memory_store
        store = get_memory_store()
        if store is None:
            return
        store.record_event(
            source="psyche-chain", event_type="behavior_action",
            data={"actor": str(actor or "?")[:80], "kind": kind,
                  "action": action, "planned": bool(planned),
                  "decision_id": decision_id, "worm": worm},
            severity="info", tags=["psyche", "behavior-chain"])
    except Exception as exc:
        note_failure("delegation:chain-record", exc)


def ask_owner(question: str, *,
              decision_id: Optional[str] = None,
              worm: Optional[str] = None,
              timeout_s: float = DEFAULT_TIMEOUT_S,
              bus: Any = None) -> Any:
    """Park the calling (worker) thread until the owner answers.

    Returns the answer object. Raises ``ClarificationTimeout`` on deadline.
    Works without a bus (rendezvous is in-process); the bus announcement is
    how owners discover the question.
    """
    q = str(question)[:MAX_QUESTION_CHARS]
    if timeout_s is None or float(timeout_s) <= 0:
        raise ClarificationTimeout(q, decision_id=decision_id, worm=worm,
                                   waited_s=0.0)
    did = decision_id or _new_decision_id()
    ev = threading.Event()
    with _lock:
        _waiters[did] = [ev, None, False]
    payload: Dict[str, Any] = {
        "question": q, "decision_id": did, "worm": worm,
        "timeout_s": float(timeout_s),
    }
    # Person enrichment (PSYCHE integration): when a fitted theta snapshot
    # exists, the owner side sees who it is answering for. Absent by default;
    # the rendezvous below is identical either way (fail-open, never blocks).
    person = _person_context()
    if person:
        payload["person"] = person
    _announce("%s.%s" % (QUESTION_TOPIC, worm or "unknown"), payload, bus)
    _chain_record(worm or "worm", "persist", "defer", True, did, worm)
    t0 = time.monotonic()
    got = ev.wait(float(timeout_s))
    with _lock:
        slot = _waiters.pop(did, None)
    waited = time.monotonic() - t0
    if got and slot is not None and slot[2]:
        return slot[1]
    _chain_record("owner", "fair", "withhold", False, did, worm)
    raise ClarificationTimeout(q, decision_id=did, worm=worm, waited_s=waited)


def answer_question(decision_id: str, answer: Any,
                    bus: Any = None) -> bool:
    """Deliver an owner's answer. Returns True if a waiter received it."""
    if not decision_id:
        return False
    with _lock:
        slot = _waiters.get(decision_id)
        if slot is None:
            return False
        slot[1] = answer
        slot[2] = True
        slot[0].set()
    _announce("%s.%s" % (ANSWER_TOPIC, decision_id),
              {"decision_id": decision_id, "answered": True}, bus)
    _chain_record("owner", "fair", "share", True, decision_id)
    return True


def pending_count() -> int:
    """Parked workers currently awaiting answers. For monitors/tests."""
    with _lock:
        return len(_waiters)


def on_question(callback: Callable[[Any], None], *,
                bus: Any = None,
                pattern: str = "worm.question.>") -> Optional[int]:
    """Subscribe an owner-side callback to question announcements.

    Returns the subscription id, or None when no bus is available. The
    callback receives the bus message; answering is via ``answer_question``.
    """
    try:
        if bus is None:
            try:
                from CORE.MemoryBus import MemoryBus
                bus = MemoryBus()
            except Exception as exc:
                note_failure("delegation:L201", exc)
                return None
        return int(bus.subscribe(pattern, callback))
    except Exception as exc:
        note_failure("delegation:L204", exc)
        return None


def clarification_outcome(name: str,
                          exc: Exception,
                          duration_ms: float = 0.0) -> Dict[str, Any]:
    """Normalise a clarification outcome to the executor result shape.

    Matches ``WormExecutor._execute_direct`` output so telemetry, breaker
    accounting and ``aggregate_results`` keep working unchanged. success is
    False (no work completed) but the error names the question — a parked
    wait is not a crash, and must not read as one in the outcome tap.
    """
    timed_out = isinstance(exc, ClarificationTimeout)
    question = getattr(exc, "question", str(exc))[:MAX_QUESTION_CHARS]
    did = getattr(exc, "decision_id", None)
    error = ("clarification timed out, no answer: %s" % question
             if timed_out else "needs owner input: %s" % question)
    return {
        "name": name,
        "success": False,
        "duration_ms": float(duration_ms or 0.0),
        "result": {"needs_input": question,
                   "timed_out": bool(timed_out),
                   "decision_id": did},
        "error": error[:MAX_QUESTION_CHARS],
        "needs_input": question,
        "timed_out": bool(timed_out),
        "decision_id": did,
    }
