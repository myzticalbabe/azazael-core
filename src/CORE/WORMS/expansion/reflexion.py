"""Reflexion verbal buffer (2026-09-29, stdlib only, fail-open).

Verbal reinforcement without weight updates (Reflexion, 2303.11366):
after a failed trajectory the agent writes one paragraph — what went
wrong, what to try differently — stored as episodic memory and prepended
to the next attempt. Actor / Evaluator / Self-Reflection split is the
caller's job; this module is only the buffer: append + recall.

Free-Jev: local tap only, no model calls. Recording must never break a
caller: every entry point returns a bool / list, never raises.
"""
from __future__ import annotations

from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

EVENT_TYPE = "reflexion_note"
MAX_TEXT_CHARS = 2000

#: EP345 (pivotal-turn location + credit assignment). The prune half and the
#: prefix-reuse half already ship in `CORE/dispatch_plan.py`: `record_backtrack`
#: prunes a branch and remembers its lesson, `reuse_prefix` reads that lesson back
#: and reports the isolation rule. What was missing here is the ability to say
#: WHICH turn was decisive and to weight credit by distance from it, which is what
#: this block adds. Bounded and stdlib-only, like the rest of the module.
MAX_TURNS = 200

#: Fields a turn record may use to declare failure. Ordered, first match wins, so
#: a caller can pass plain dicts without agreeing on a schema first.
_FAILURE_KEYS = ("ok", "success", "passed", "won")
_ERROR_KEYS = ("error", "failed", "exception", "blocked", "gap")


def _turn_failed(turn: Any) -> bool:
    """True if one turn record reads as a failure. Tolerates any shape.

    A record that says nothing about success or failure is NOT counted as a
    failure: counting it as one would manufacture a pivotal turn out of an
    untyped row, and a manufactured pivot silently corrupts the credit vector.
    """
    if isinstance(turn, bool):
        return not turn
    if isinstance(turn, dict):
        for _key in _FAILURE_KEYS:
            if _key in turn:
                return not bool(turn[_key])
        for _key in _ERROR_KEYS:
            if turn.get(_key):
                return True
        return False
    return not bool(turn)


def _as_turns(turns: Any) -> list:
    """Coerce a trajectory into a bounded list. Never raises."""
    try:
        return list(turns)[:MAX_TURNS]
    except TypeError as exc:
        note_failure("expansion/reflexion:turns", exc)
        return []


def locate_pivotal_turn(turns: Any) -> int | None:
    """Index of the earliest failure the trajectory never recovered from.

    The classical pivotal-turn reading - a decisive early mistake, not merely the
    last one: the first index of the trailing failure block. Returns None when the
    run recovered after its last failure, when nothing failed, or when the input
    cannot be read as a sequence; None therefore means "no pivotal turn", never
    "could not tell". Never raises.
    """
    seq = _as_turns(turns)
    if not seq:
        return None
    last_failure = None
    for _i, _turn in enumerate(seq):
        if _turn_failed(_turn):
            last_failure = _i
    if last_failure is None or last_failure != len(seq) - 1:
        return None
    _start = last_failure
    while _start - 1 >= 0 and _turn_failed(seq[_start - 1]):
        _start -= 1
    return _start


def pivotal_credit_weights(turns: Any, pivotal: int | None = None,
                           decay: float = 0.5) -> list[float]:
    """Per-turn credit for one trajectory (EP345's credit-assignment half).

    The pivotal turn carries the largest weight; prefix turns keep credit decaying
    with distance, so the turn nearest the pivot is credited most; every SUFFIX
    turn after the pivot is 0.0 - quarantined, which is the isolation rule
    `dispatch_plan.reuse_prefix` reports to its caller, expressed as numbers a
    learner can consume. Weights sum to 1.0.

    Returns [] when there is no pivotal turn to assign against (nothing is
    invented) or when the pivot is out of range. Never raises.
    """
    seq = _as_turns(turns)
    if not seq:
        return []
    pivot = locate_pivotal_turn(seq) if pivotal is None else pivotal
    if pivot is None:
        # Nothing pivotal: the docstring above already promises []. The old
        # `else 0` invented turn 0 and returned [1.0] for an all-success
        # trajectory, i.e. credited a turn that was never decisive.
        return []
    try:
        pivot = int(pivot)
    except (TypeError, ValueError) as exc:
        note_failure("expansion/reflexion:credit-pivot", exc)
        return []
    if not 0 <= pivot < len(seq):
        return []
    raw = [0.0] * len(seq)
    weight = 1.0
    for _i in range(pivot, -1, -1):
        raw[_i] = weight
        weight *= decay
    total = sum(raw)
    if total <= 0:
        return []
    return [round(_v / total, 6) for _v in raw]


def record_reflection(question: str, what_failed: str, what_next: str,
                      *, store: Any = None,
                      decision_id: str | None = None,
                      outcome: str | None = None,
                      predicted: float | None = None,
                      realised: float | None = None,
                      turns: Any = None,
                      pivotal_turn: int | None = None) -> bool:
    """Append one verbal reflection. Returns True if written.

    `outcome` / `predicted` / `realised` are optional and purely additive
    (EP294, RefGRPO reflection calibration). A reflection that carries the
    caller's own confidence BEFORE the attempt (`predicted`) next to what
    actually happened (`realised`) can be scored against reality later, which
    is the difference between a verbal buffer and a calibrated one. Every one
    of them defaults to None, so a caller passing only the original three
    arguments gets byte-identical behaviour.

    `turns` / `pivotal_turn` are the same shape of addition (EP345): pass the
    trajectory and the buffer LOCATES the decisive turn itself - the first index of
    the failing tail the run never recovered from - and stores `turns_analysed`,
    `pivotal_turn`, `n_turns` and a normalised `credit` vector in which every
    suffix turn after the pivot is 0.0, which is the isolation rule
    `dispatch_plan.reuse_prefix` reports, as numbers. Pass `pivotal_turn` to
    override the located index. Supplying neither writes none of those keys, so
    an old row and a pivoted row stay distinguishable.
    """
    text_failed = str(what_failed or "")[:MAX_TEXT_CHARS]
    text_next = str(what_next or "")[:MAX_TEXT_CHARS]
    if not text_failed and not text_next:
        return False
    try:
        if store is None:
            from CORE.local_memory import get_memory_store
            store = get_memory_store()
        data: dict[str, Any] = {
            "question": str(question or "")[:500],
            "what_failed": text_failed,
            "what_next": text_next,
        }
        if decision_id:
            data["decision_id"] = str(decision_id)
        # Additive calibration fields: only written when actually supplied, so
        # a pre-existing row and a new row stay distinguishable.
        for _key, _value in (("outcome", outcome), ("predicted", predicted),
                             ("realised", realised)):
            if _value is not None:
                data[_key] = _value
        # EP345: when the caller hands over the trajectory, locate the pivotal
        # turn here rather than making every caller re-derive it, and store the
        # assignment beside the note.
        if turns is not None:
            _seq = _as_turns(turns)
            _pivot = None
            if pivotal_turn is not None:
                try:
                    _pivot = int(pivotal_turn)
                except (TypeError, ValueError) as exc:
                    note_failure("expansion/reflexion:pivotal-arg", exc)
            if _pivot is None:
                _pivot = locate_pivotal_turn(_seq)
            data["turns_analysed"] = len(_seq)
            if _pivot is not None:
                data["pivotal_turn"] = int(_pivot)
                data["n_turns"] = len(_seq)
                _credit = pivotal_credit_weights(_seq, _pivot)
                if _credit:
                    data["credit"] = _credit
        store.record_event(
            source="reflexion",
            event_type=EVENT_TYPE,
            data=data,
            severity="info",
            tags=["reflexion", "verbal-rl"],
            correlation_id=str(decision_id) if decision_id else None,
        )
        return True
    except Exception as exc:
        note_failure("reflexion:L66", exc)
        return False


def reflections_for(question: str, *, store: Any = None,
                    limit: int = 5) -> list[dict[str, Any]]:
    """Recall up to ``limit`` reflections for a question (utility-weighted).

    Newest-first within each rank. Reflections that carry a `realised` outcome
    are promoted ahead of ones that do not (EP294): a reflection that was
    scored against reality is better evidence for the next attempt than one
    that was never checked. Rows without an outcome are still returned - they
    are simply ranked below - so this is a re-ordering, never a filter.
    """
    try:
        if store is None:
            from CORE.local_memory import get_memory_store
            store = get_memory_store()
        # `store` is injectable (this module is used with test doubles), so
        # prefer the paged reader when the store has one and fall back to the
        # plain read otherwise. fetch_all_memories is required on a real store:
        # query_memories clamps at MAX_QUERY_LIMIT (500), which would make
        # recall silently ignore every reflection older than the newest 500.
        rows: Any = []
        fetch = getattr(store, "fetch_all_memories", None)
        if callable(fetch):
            rows = fetch(event_type=EVENT_TYPE, limit=500) or []
        else:
            rows = store.query_memories(event_type=EVENT_TYPE, limit=500) or []
    except Exception as exc:
        note_failure("reflexion:L94", exc)
        return []
    q = " ".join(str(question or "").lower().split())
    hits = []
    for row in rows:
        d = (row or {}).get("data") or {}
        if isinstance(d, str):
            try:
                import json as _json
                d = _json.loads(d)
            except Exception as exc:
                note_failure("reflexion:L104", exc)
                continue
        if not isinstance(d, dict):
            continue
        if q and q not in str(d.get("question", "")).lower():
            continue
        hits.append({"question": d.get("question"),
                     "what_failed": d.get("what_failed"),
                     "what_next": d.get("what_next"),
                     "decision_id": d.get("decision_id"),
                     "outcome": d.get("outcome"),
                     "predicted": d.get("predicted"),
                     "realised": d.get("realised")})
        # EP345 fields ride along only when the row actually carries them, so a
        # pre-existing reflection keeps exactly the key set it always had.
        _hit = hits[-1]
        for _key in ("pivotal_turn", "n_turns", "turns_analysed"):
            if d.get(_key) is not None:
                _hit[_key] = d.get(_key)

    # Calibration-error weighting (EP294, Oct-10 build): realised rows come
    # first, ordered by |predicted - realised| ascending (best-calibrated
    # first) when both exist; realised-without-predicted next; unchecked rows
    # last. The sort is stable and `rows` arrives newest-first, so recency
    # order is preserved inside every rank.
    def _calibration_key(h: dict) -> tuple:
        realised = h.get("realised")
        predicted = h.get("predicted")
        if realised is None:
            return (2, 0.0)
        if predicted is None:
            return (1, 0.0)
        try:
            return (0, abs(float(predicted) - float(realised)))
        except (TypeError, ValueError) as exc:
            note_failure("expansion/reflexion:144", exc)
            return (1, 0.0)

    def _pivot_key(h: dict) -> tuple:
        """Pivoted rows first, then the EARLIER relative pivot (EP345).

        A located pivot is evidence the failure was localised; among localised
        rows the earlier pivot leaves more reusable prefix. Rows with no located
        pivot sort last inside their calibration rank.
        """
        pivot = h.get("pivotal_turn")
        if pivot is None:
            return (1, 0.0)
        try:
            total = float(h.get("n_turns") or 0)
            rel = float(pivot) / total if total else 0.0
        except (TypeError, ValueError) as exc:
            note_failure("expansion/reflexion:pivot-key", exc)
            rel = 0.0
        return (0, rel)

    # Two stable passes: calibration is primary, the located pivot is the
    # tie-break. Both sorts are stable, so recency order survives inside a rank.
    hits.sort(key=_pivot_key)
    hits.sort(key=_calibration_key)
    return hits[:max(1, int(limit))]
