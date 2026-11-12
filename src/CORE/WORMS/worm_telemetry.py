"""One canonical per-worm outcome emitter.

WHY THIS EXISTS (2026-09-21)
----------------------------
The outcome tap is the input to evolution, and it covered almost nothing:

    rows: 4,286   worm types: 25   evolvable organs: 211

86.5% of those rows came from three nature worms, so fitness was selecting on
~7% of the fleet -- which caps every genotype rung regardless of how good the
genome is.

The cause was not data volume. It was that only SOME execution paths wrote a
row at all, and the ones that did had each grown their own payload:

    ClusterWORMZ._execute_one      emitted, own payload
    ClusterWORMZ.execute_worm      did NOT       (fixed 2026-09-21)
    ClusterWORMZ._record_outcome  emitted, own payload   (4th schema)
    CONCURRENCY.WormExecutor       did NOT  (an entire second executor family)

A schema with four producers is four schemas, and two of them were silent. So
this module is the single emitter: stdlib-only, fail-soft, imported lazily by
every executor. Adding a producer means calling this, not copying a payload.

DESTINATION
-----------
Writes via `CORE.local_memory.get_memory_store().record_event(...)`. The PAMS
path (`PamsRecorder.record_event`) already falls back to the SAME
`LocalMemoryStore.record_event` with the same keyword signature, so routing a
PAMS-based producer here is destination-identical rather than a migration.

`decision_id` is optional and is the join key back to the choice that caused the
execution (see `spawn_credit`). Rows without it are legacy and can only be
joined per worm type, never per decision.

NAMING -- do not re-introduce the collision
-------------------------------------------
`CORE.metrics` already exports a DIFFERENT function called
`record_worm_execution(name, success, duration_ms, error)` -- a positional
in-process counter used by `azazael-ctl metrics`, listed in that module's
`__all__`. The first version of this module reused that name with an
incompatible signature, i.e. it recreated the exact "several things, one name,
different shapes" condition it was written to fix. Hence `emit_*` here: distinct
verb, no shadowing, and `test_worm_telemetry.py` pins that the two APIs stay
distinguishable.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

#: The canonical event name. Both producers and `spawn_credit` read this.
EVENT_TYPE = "clusterwormz_worm_executed"

#: Longest error string recorded. Truncation is deliberate: a stack trace is
#: not a signature, and unbounded text in a hot path is how a tap becomes slow
#: enough that someone disables it.
MAX_ERROR_CHARS = 300


def emit_worm_outcome(
    name: str,
    result: Optional[Dict[str, Any]] = None,
    *,
    cluster: Any = None,
    decision_id: Optional[str] = None,
    nature: Optional[str] = None,
    source: str = "ClusterWORMZ",
    success: Optional[bool] = None,
    duration_ms: Optional[float] = None,
    error: Optional[str] = None,
    standby: Optional[bool] = None,
) -> bool:
    """Record ONE per-worm execution row. Returns True if it was written.

    Accepts either a result dict or explicit fields, because the executors do
    not agree on a result type (`CONCURRENCY` returns an `ExecutionResult`
    dataclass). Normalising here is the point: one shape reaches the store.

    Never raises. Telemetry must never be able to break execution, so a failure
    returns False and the caller proceeds -- but a failure is VISIBLE to the
    caller rather than swallowed into a silent `pass`.
    """
    if not name:
        return False

    if success is None:
        result = result or {}
        success = bool(result.get("success", False))
    if duration_ms is None:
        result = result or {}
        try:
            duration_ms = float(result.get("duration_ms", 0.0) or 0.0)
        except (TypeError, ValueError) as exc:
            note_failure("worm_telemetry:L94", exc)
            duration_ms = 0.0
    if error is None:
        result = result or {}
        raw_error = result.get("error")
        error = str(raw_error) if raw_error else None
    if standby is None:
        result = result or {}
        standby = bool(result.get("standby", False))
    # Standby is not success: a worm that did not run must not read as a
    # passing check in win-rate routing (PivoARL/Auto forensic 2026-09-29).
    if standby:
        success = False

    # Additive gap hunt (2026-10-17): the two-interpreter gate
    # (ci_two_interpreter_gate) compares per-interpreter outcomes, but the
    # tap never records WHICH interpreter produced the row. One short
    # version string closes that join. stdlib-only; never raises (guarded).
    try:
        import sys as _sys  # noqa: PLC0415 (lazy: keep leaf import-light)
        _interp = str(getattr(_sys, "version", "").split()[0] or "?")
        _gil = "gil-disabled" if getattr(_sys, "_is_gil_enabled", None) is not None and not _sys._is_gil_enabled() else "gil"
    except Exception:  # noqa: BLE001 - telemetry must never break execution
        _interp, _gil = "?", "?"
    data: Dict[str, Any] = {
        "worm": name,
        "success": bool(success),
        "standby": bool(standby),
        "duration_ms": float(duration_ms or 0.0),
        "error": str(error)[:MAX_ERROR_CHARS] if error else None,
        "interp": _interp,
        "gil": _gil,
    }
    if nature:
        data["nature"] = str(nature)
    if decision_id:
        data["decision_id"] = str(decision_id)

    for attr, key in (("cluster_type", "cluster_type"),
                      ("cluster_id", "cluster_id"),
                      ("cluster_name", "cluster_id"),
                      ("nature", "nature")):
        val = getattr(cluster, attr, None)
        if val is not None and key not in data:
            data[key] = str(val)

    try:
        from CORE.local_memory import get_memory_store
        store = get_memory_store()
        store.record_event(
            source=source,
            event_type=EVENT_TYPE,
            data=data,
            severity="error" if not data["success"] else "info",
            tags=["cluster", "worm-telemetry"],
        )
        return True
    except Exception as exc:
        note_failure("worm_telemetry:L139", exc)
        return False


def emit_worm_outcome_from_result(
    name: str,
    result: Any,
    *,
    decision_id: Optional[str] = None,
    nature: Optional[str] = None,
    source: str = "ConcurrencyWormExecutor",
) -> bool:
    """Record from a non-dict result object (e.g. `ExecutionResult`).

    Kept separate so the dict path above stays allocation-free for the executors
    that already hold a dict.
    """
    try:
        return emit_worm_outcome(
            name,
            decision_id=decision_id,
            nature=nature,
            source=source,
            success=bool(getattr(result, "success", False)),
            duration_ms=float(getattr(result, "duration_ms", 0.0) or 0.0),
            error=getattr(result, "error", None),
            standby=bool(getattr(result, "standby", False)),
        )
    except Exception as exc:
        note_failure("worm_telemetry:L167", exc)
        return False
