# coding: utf-8
"""AOA delegation tracer — one durable span per routed delegation.

WHAT THIS IS FOR
----------------
``CrossArmRouter.route_task`` already decides, executes, and grades an outcome:
``flat["outcome"]`` carries ``verdict`` (accepted / rejected / unverified),
``judged_by``, ``success``, ``duration_ms`` and the contract violations. Then the
record dies in process memory:

* ``_record_decision`` appends to a bounded deque,
* ``ContractViolationLog.record`` keeps at most 100 in-memory entries and is
  warn-only,
* nothing writes the outcome anywhere durable.

So the question the project has been trying to answer for weeks -- *was this arm
the right choice for this capability* -- cannot be answered from a ledger, only
from a live process that happens to still be running. This module writes the
outcome to the span table AOA already has, so the answer survives the process.

WHY NOT THE OTel SDK
--------------------
``CORE.cd3_exporter`` shipped a ``SpanExporter`` and a
``configure_otel_tracing()`` helper, and neither has a production caller; the SDK
is not installed on the production interpreter, so no span was ever produced
(``otel_spans``: 0 rows in the live ``telemetry_store.db``). Rather than make
telemetry depend on an install that does not exist, this module uses
``CORE.cd3_exporter.record_span`` -- the direct, SDK-free writer that targets the
same table ``export()`` does. If the SDK is installed later, its spans and these
land in the same rows and ``query_trace_by_id`` reads both.

ATTRIBUTE NAMES ARE THE DRAFT, NOT INVENTED HERE
------------------------------------------------
The names come from ``docs/OSS_OTEL_SEMCONV_AGENT_DELEGATION_2026-11-02.md``:
ten field-mapping attributes (five set at delegation time, five at completion)
plus three W3C trace-correlation attributes. They are emitted as recorded, so the
draft and the code cannot drift apart silently.

WHAT IT REFUSES TO CLAIM
------------------------
A span is a record, not a verdict. ``verdict`` here is the router's own
assessment of its own call (``judged_by: aoa.router``) -- it says an arm returned
``ok``, not that the arm was *correct*. Nothing in this module measures whether
an outcome was right; it measures what was decided and what came back.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).
try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

try:
    from CORE.cd3_exporter import query_trace_by_id, record_span
except ImportError as _cd3_missing:               # src not on sys.path
    note_failure("delegation_tracer:cd3-import", _cd3_missing)
    record_span = None                            # type: ignore[assignment]
    query_trace_by_id = None                      # type: ignore[assignment]

try:
    from .contracts import verdict_agreement
except ImportError:                               # imported as a top-level module
    from core.contracts import verdict_agreement  # type: ignore[no-redef]


# -- Semantic convention attribute names (docs/OSS_OTEL_SEMCONV_...) ----------
A_DELEGATION_INSTRUCTION = "agent.delegation.instruction"
A_DELEGATION_CONTEXT = "agent.delegation.context"
A_DELEGATION_ARM_ID = "agent.delegation.arm_id"
A_DELEGATION_CAPABILITY = "agent.delegation.capability"
A_DELEGATION_MODEL = "agent.delegation.model"
A_DELEGATION_PROVENANCE = "agent.delegation.provenance"
A_DELEGATION_STALE_AFTER_S = "agent.delegation.stale_after_s"
A_DELEGATION_PRODUCED_AT = "agent.delegation.produced_at"
A_DELEGATION_IS_STALE = "agent.delegation.is_stale"
A_DELEGATION_STATUS = "agent.delegation.status"
A_DELEGATION_TRACE_ID = "agent.delegation.trace_id"
A_DELEGATION_SPAN_ID = "agent.delegation.span_id"
A_DELEGATION_PARENT_SPAN_ID = "agent.delegation.parent_span_id"

#: Router-owned additions. Not in the draft, which has no field for the verdict
#: the router already computes -- recording it here rather than not at all.
A_DELEGATION_VERDICT = "agent.delegation.verdict"
A_DELEGATION_SUCCESS = "agent.delegation.success"
A_DELEGATION_DURATION_MS = "agent.delegation.duration_ms"
A_DELEGATION_VIOLATIONS = "agent.delegation.contract_violations"

#: The Arm's own claim about its answer. Kept in separate attributes from the
#: router's verdict on purpose: merging them would lose the only reading that
#: matters -- an arm that returns ``ok`` while saying it got the answer wrong.
A_DELEGATION_ARM_VERDICT = "agent.delegation.arm_verdict"
A_DELEGATION_ARM_CONFIDENCE = "agent.delegation.arm_verdict_confidence"
A_DELEGATION_ARM_JUDGED_BY = "agent.delegation.arm_verdict_judged_by"
A_DELEGATION_VERDICT_AGREEMENT = "agent.delegation.verdict_agreement"

#: Everything emitted here that the draft does not define. Named as one tuple so
#: the extension is visible in the code rather than only in this comment.
EXTENSION_ATTRIBUTES: tuple[str, ...] = (
    A_DELEGATION_VERDICT,
    A_DELEGATION_SUCCESS,
    A_DELEGATION_DURATION_MS,
    A_DELEGATION_VIOLATIONS,
    A_DELEGATION_ARM_VERDICT,
    A_DELEGATION_ARM_CONFIDENCE,
    A_DELEGATION_ARM_JUDGED_BY,
    A_DELEGATION_VERDICT_AGREEMENT,
)

#: The ten field-mapping attributes from the draft, then the three correlation
#: ones. Kept as named tuples so a test can assert the set, rather than asserting
#: whatever the implementation happens to emit.
SEMCONV_FIELD_ATTRIBUTES: tuple[str, ...] = (
    A_DELEGATION_INSTRUCTION,
    A_DELEGATION_CONTEXT,
    A_DELEGATION_ARM_ID,
    A_DELEGATION_CAPABILITY,
    A_DELEGATION_MODEL,
    A_DELEGATION_PROVENANCE,
    A_DELEGATION_STALE_AFTER_S,
    A_DELEGATION_PRODUCED_AT,
    A_DELEGATION_IS_STALE,
    A_DELEGATION_STATUS,
)
SEMCONV_CORRELATION_ATTRIBUTES: tuple[str, ...] = (
    A_DELEGATION_TRACE_ID,
    A_DELEGATION_SPAN_ID,
    A_DELEGATION_PARENT_SPAN_ID,
)

#: OTel StatusCode values, as ``cd3_exporter.export()`` records them.
STATUS_UNSET, STATUS_OK, STATUS_ERROR = 0, 1, 2

#: Environment switch. Off is the escape hatch for a caller that measures its own
#: latencies and does not want a SQLite write on the routing path.
_TRACING_ENV = "AZAZAEL_AOA_TRACING"
_OFF = {"0", "off", "false", "no", "disabled"}


def tracing_enabled() -> bool:
    """Is span recording on? On by default; ``AZAZAEL_AOA_TRACING=off`` disables."""
    return str(os.environ.get(_TRACING_ENV, "on")).strip().lower() not in _OFF


def new_trace_id() -> str:
    """32 lowercase hex characters, W3C-shaped."""
    return uuid.uuid4().hex


def new_span_id() -> str:
    """16 lowercase hex characters, W3C-shaped."""
    return uuid.uuid4().hex[:16]


def _jsonable(value: Any) -> str:
    """Serialise a context/evidence snapshot, never raising on odd values."""
    try:
        return json.dumps(value, default=str, sort_keys=True)
    except Exception as exc:                      # pragma: no cover - defensive
        note_failure("delegation_tracer:_jsonable", exc)
        return "{}"


def build_attributes(
    flat: Dict[str, Any],
    *,
    capability: str,
    arm_id: str,
    spec: Any = None,
    trace_id: str = "",
    span_id: str = "",
    parent_span_id: Optional[str] = None,
    duration_ms: Optional[float] = None,
) -> Dict[str, Any]:
    """Map one routed delegation onto the semconv attribute set.

    ``flat`` is the router's result dict (``status``, ``is_stale``,
    ``produced_at``, ``provenance``, ``stale_after_s``, ``outcome``). ``spec`` is
    the ``DelegationSpec`` when one was built, and may be None -- a pinned call
    whose spec construction failed still produced an outcome worth recording.

    Absent values are *omitted*, not written as ``null``: the same rule
    ``DelegationSpec._tracing()`` follows, so a consumer comparing or hashing the
    attribute set sees byte-identical input whether or not a field was known.

    ``agent.delegation.is_stale`` is the one exception, and it is deliberate:
    the draft defines it as three states -- true, false, and null for
    *undecidable* -- so null there is a reading rather than a missing value.
    """
    _raw_outcome = flat.get("outcome")
    outcome: Dict[str, Any] = _raw_outcome if isinstance(_raw_outcome, dict) else {}
    status = flat.get("status") or "error"
    if duration_ms is None:
        duration_ms = outcome.get("duration_ms")

    attrs: Dict[str, Any] = {
        A_DELEGATION_ARM_ID: str(arm_id or ""),
        A_DELEGATION_CAPABILITY: str(capability or ""),
        A_DELEGATION_STATUS: str(status),
    }

    # -- delegation-time fields, from the spec when there is one ------------
    if spec is not None:
        attrs[A_DELEGATION_INSTRUCTION] = str(getattr(spec, "instruction", "") or "")
        attrs[A_DELEGATION_CONTEXT] = _jsonable(getattr(spec, "context", {}) or {})
        model = getattr(spec, "model", None)
        if model is not None:
            attrs[A_DELEGATION_MODEL] = str(model)

    # -- completion-time fields ---------------------------------------------
    provenance = flat.get("provenance")
    if provenance is not None:
        attrs[A_DELEGATION_PROVENANCE] = str(provenance)
    stale_after = flat.get("stale_after_s")
    if stale_after is not None:
        attrs[A_DELEGATION_STALE_AFTER_S] = float(stale_after)
    produced = flat.get("produced_at")
    if produced is not None:
        attrs[A_DELEGATION_PRODUCED_AT] = str(produced)
    # Three states: true / false / null(undecidable). null is recorded as
    # undecidable rather than dropped, because "we did not know" is a reading --
    # the only field where absence and null mean different things.
    is_stale = flat.get("is_stale")
    attrs[A_DELEGATION_IS_STALE] = None if is_stale is None else bool(is_stale)

    # -- router-owned outcome ------------------------------------------------
    if outcome:
        verdict = outcome.get("verdict")
        if verdict is not None:
            attrs[A_DELEGATION_VERDICT] = str(verdict)
        if "success" in outcome:
            attrs[A_DELEGATION_SUCCESS] = bool(outcome.get("success"))
        violations = outcome.get("contract_violations")
        if violations:
            attrs[A_DELEGATION_VIOLATIONS] = ",".join(str(v) for v in violations)
    if duration_ms is not None:
        attrs[A_DELEGATION_DURATION_MS] = float(duration_ms)

    # -- the Arm's own claim, and whether it agrees with the router ----------
    _raw_arm_verdict = flat.get("arm_verdict")
    arm_verdict: Optional[Dict[str, Any]] = (
        _raw_arm_verdict if isinstance(_raw_arm_verdict, dict) else None)
    if arm_verdict:
        claimed = arm_verdict.get("verdict")
        if claimed is not None:
            attrs[A_DELEGATION_ARM_VERDICT] = str(claimed)
        confidence = arm_verdict.get("confidence")
        if confidence is not None:
            attrs[A_DELEGATION_ARM_CONFIDENCE] = float(confidence)
        judged_by = arm_verdict.get("judged_by")
        if judged_by:
            attrs[A_DELEGATION_ARM_JUDGED_BY] = str(judged_by)
    # Always written, ``unknown`` included: a uniform field whose "unknown" means
    # "nobody self-assessed" is readable; an absent one cannot be counted.
    attrs[A_DELEGATION_VERDICT_AGREEMENT] = verdict_agreement(
        outcome.get("verdict"), arm_verdict)

    # -- correlation ---------------------------------------------------------
    if trace_id:
        attrs[A_DELEGATION_TRACE_ID] = trace_id
    if span_id:
        attrs[A_DELEGATION_SPAN_ID] = span_id
    if parent_span_id:
        attrs[A_DELEGATION_PARENT_SPAN_ID] = parent_span_id

    return attrs


def emit_delegation(
    flat: Dict[str, Any],
    *,
    capability: str,
    arm_id: str,
    spec: Any = None,
    duration_ms: Optional[float] = None,
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
    parent_span_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """Record one delegated call as a span. Returns the ids, or None.

    None means *nothing was written* -- tracing disabled, no writer importable,
    or the write failed -- and it is never an exception. The router calls this
    after it has already produced an answer; telemetry must not turn a working
    route into a failed one.

    A ``trace_id`` on the spec wins over the ``trace_id`` argument, and a spec
    with a ``span_id`` keeps it, so a chain built with ``spawn_child()`` keeps its
    parent links instead of being flattened into fresh ones per call.
    """
    if not tracing_enabled():
        return None
    if record_span is None:
        return None

    spec_trace = getattr(spec, "trace_id", None) if spec is not None else None
    spec_span = getattr(spec, "span_id", None) if spec is not None else None
    spec_parent = getattr(spec, "parent_span_id", None) if spec is not None else None

    tid = spec_trace or trace_id or new_trace_id()
    sid = spec_span or span_id or new_span_id()
    pid = spec_parent if spec_parent is not None else parent_span_id

    attrs = build_attributes(
        flat, capability=capability, arm_id=arm_id, spec=spec,
        trace_id=tid, span_id=sid, parent_span_id=pid, duration_ms=duration_ms,
    )

    # End is "now"; start is derived from the measured duration so the span's
    # width is the router's own timer rather than a second, different clock.
    end_ns = time.time_ns()
    width_ns = int(max(0.0, float(duration_ms or 0.0)) * 1_000_000)
    start_ns = end_ns - width_ns

    ok = record_span(
        trace_id=tid,
        span_id=sid,
        name="delegation %s -> %s" % (capability or "?", arm_id or "?"),
        start_time=start_ns,
        end_time=end_ns,
        parent_span_id=pid,
        status_code=STATUS_OK if str(flat.get("status") or "") == "ok" else STATUS_ERROR,
        attributes=attrs,
        db_path=db_path,
    )
    return {"trace_id": tid, "span_id": sid} if ok else None


def delegation_outcomes(
    trace_id: str, db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Read a delegation trace back as outcomes, oldest first.

    This is the query that answers the question the spans exist for: for each
    delegation in the trace -- which arm, for which capability, with what verdict,
    how long, and was the result stale. Returns ``[]`` for an unknown trace id;
    an empty list is a reading, not an error.

    The returned ``verdict`` is the router's own judgement of ``status``, restated
    here so a reader does not have to re-derive it: a span is not evidence that
    the arm was the *right* choice, only that it was the chosen one.
    """
    if query_trace_by_id is None or not trace_id:
        return []
    try:
        rows = query_trace_by_id(trace_id, db_path=db_path)
    except Exception as exc:
        note_failure("delegation_tracer:delegation_outcomes", exc)
        return []

    out: List[Dict[str, Any]] = []
    for row in rows:
        try:
            attrs = json.loads(row.get("attributes") or "{}")
        except Exception as exc:
            note_failure("delegation_tracer:delegation_outcomes:attrs", exc)
            attrs = {}
        out.append({
            "span_id": attrs.get(A_DELEGATION_SPAN_ID),
            "parent_span_id": row.get("parent_span_id"),
            "name": row.get("name"),
            "arm_id": attrs.get(A_DELEGATION_ARM_ID),
            "capability": attrs.get(A_DELEGATION_CAPABILITY),
            "status": attrs.get(A_DELEGATION_STATUS),
            "verdict": attrs.get(A_DELEGATION_VERDICT),
            "success": attrs.get(A_DELEGATION_SUCCESS),
            "duration_ms": attrs.get(A_DELEGATION_DURATION_MS,
                                     row.get("duration_ms")),
            "is_stale": attrs.get(A_DELEGATION_IS_STALE),
            "provenance": attrs.get(A_DELEGATION_PROVENANCE),
            "violations": attrs.get(A_DELEGATION_VIOLATIONS),
            # The Arm's claim, kept next to -- never merged into -- the router's
            # verdict above, plus the reading of the pair.
            "arm_verdict": attrs.get(A_DELEGATION_ARM_VERDICT),
            "arm_confidence": attrs.get(A_DELEGATION_ARM_CONFIDENCE),
            "arm_judged_by": attrs.get(A_DELEGATION_ARM_JUDGED_BY),
            "agreement": attrs.get(A_DELEGATION_VERDICT_AGREEMENT),
        })
    return out
