#!/usr/bin/env python3
"""technique_runtime.py — exercise registry + fail-open boundary helpers for the harvested techniques.

Why this exists
---------------
`state/SOTA_FULL_REVIEW_2026-10-09.md` reviews 181 harvested techniques against
this codebase. Its dominant finding is NOT missing code — it is **unreachable**
code. Read by reading, on 2026-10-09:

    verification_gate.py   9 composed detectors   1 caller  (an exercise script)
    dispatch_plan.py       plan_dag/assign_roles  2 callers (both exercises)
    reflexion.py           complete buffer        0 production callers
    skill_library.py       16 exported functions  3 importers (wiring, canon check)
    (Oct-10: 10 small hosts added — replicator/renderer/mask/miner/rules/
    shapley/distiller/skill-md/experience/witness-scan; training intake reads
    technique-link + reward-ledger + calibration rows via training_intake)

So the techniques were *implemented* and then never invoked. A capability with no
call site is indistinguishable from a missing capability, and it rots.

What this module is
-------------------
The single production surface that invokes them, plus the registry that makes the
set enumerable rather than aspirational. Every wiring is an adapter registered
under its EP code:

    from CORE.technique_runtime import register, run_all

    register("EP44", "ReAct driver loop", "CORE.structured_action:drive", fn)

`run_all()` invokes every registered adapter and reports per-EP engagement, so
"all of it is wired" is a command anyone can run and check, not a claim in a
document. Adapters live in `CORE/technique_wiring/` (one module per layer) and
register themselves on import; this file never needs to know they exist.

Contract for an adapter
-----------------------
- takes no arguments and returns a dict describing what it just did;
- is FAIL-OPEN: it catches its own exceptions and never raises into a caller;
- is READ-MOSTLY: it must be safe to invoke on a live system (the self-test runs
  every adapter, so an adapter that mutates the repo or spends money is a bug);
- names the module::symbol it invokes, so the registry is an audit trail.

Deliberately stdlib-only. No adapter here may import a heavy dependency: a wiring
that cannot run on this box is not a wiring, it is a wish.
"""
from __future__ import annotations

import importlib
import inspect
import threading
import time
from typing import Any, Callable, Dict, List

#: The ledger lives in a leaf module (stdlib only, zero CORE imports) so that
#: any module can import the recorder without risking an import cycle -- a cycle
#: caught by a guarded import would silently turn every recording into a no-op.
#: Re-exported here because callers and tests reach it as
#: `technique_runtime.note_failure` / `technique_runtime.failure_notes`.
#:
#: ORDER MATTERS, and here it is enforced BY CONSTRUCTION: this is the ONLY
#: binding of the recorder, and it is deliberately UNGUARDED. A
#: `try: from CORE.failure_ledger import note_failure / except ImportError:`
#: block used to sit BELOW this unguarded import of the same two names, which
#: made its handler unreachable: the name was either already bound (so the
#: handler could not fire) or this import had already aborted the module (so
#: execution never reached it). A guard that can never fire is a gate that can
#: never fail -- the same defect class as one that can never pass -- so it was
#: removed rather than kept as reassurance. The ledger is importable
#: unconditionally by design: `CORE/failure_ledger.py` imports nothing but the
#: standard library, and its own docstring records why a GUARDED import of the
#: recorder would silently downgrade every recording to a no-op (the recorder
#: swallowing itself). The guarded engram/verification-gate imports below still
#: record through this binding, which is why it must come first.
from CORE.failure_ledger import failure_notes, note_failure  # noqa: F401

#: The worms tree, imported at module level (fail-open) so the restored read
#: path has a caller. Guarded rather than lazy: a function-level import counts
#: against the anti-pattern ratchet, and this dependency is optional at import
#: time only in minimal environments.
try:                                                       # pragma: no cover
    from CORE.WORMS.expansion import engram as _engram
except Exception as exc:                                   # pragma: no cover
    note_failure("technique_runtime:60", exc)
    _engram = None

#: Same dependency, bound as a SYMBOL rather than reached by name through a
#: string. Registry D-1: `engram.py` imports nothing but the standard library
#: (its only `technique_runtime` mention is a comment), so this adds no cycle
#: that the `engram as _engram` import above has not already taken on.
#: `run_read_driven_consolidation` goes into `__all__` nowhere on purpose: the
#: point is that the reference is VISIBLE, not that it is re-exported.
try:                                                       # pragma: no cover
    from CORE.WORMS.expansion.engram import run_read_driven_consolidation
except Exception as exc:                                   # pragma: no cover
    note_failure("technique_runtime:64", exc)
    run_read_driven_consolidation = None

try:                                                       # pragma: no cover
    from CORE import verification_gate as _vg
except Exception as exc:                                   # pragma: no cover
    note_failure("technique_runtime:65", exc)
    _vg = None

from CORE.local_memory import get_memory_store

__all__ = [
    "register", "ADAPTERS", "load_slices", "run_all", "status",
    "verify_boundary", "reflect_on_failure", "record_link",
]

#: EP code -> {"ep", "name", "where", "fn", "layer"}. Populated by `register`
#: from the slice modules and by the direct helpers below.
ADAPTERS: Dict[str, Dict[str, Any]] = {}

#: Slice modules under CORE.technique_wiring/. Imported lazily so a slice that
#: fails to import (a missing optional dep) degrades to "that layer is not wired"
#: instead of making the whole runtime unimportable.
SLICES = (
    "_b01_05",
    "_l0_model",
    "_l1_memory",
    "_l2_skills",
    "_l3_verify",
    "_l4_exec",
    "_l5_dispatch",
    "_l6_evolve",
    "_l7_interface",
)

_loaded = False


# -- registry ------------------------------------------------------------------

def register(ep: str, name: str, where: str, fn: Callable[[], Any],
             layer: str = "") -> None:
    """Register one technique's production invocation under its EP code.

    ep:    the EP code from the review ("EP44").
    name:  the technique name (review row title).
    where: the module::symbol actually invoked — the audit trail.
    fn:    the adapter (no args, returns a dict, fail-open, read-mostly).
    """
    ep = str(ep).strip().upper()
    if not ep.startswith("EP") or not callable(fn):
        return
    if layer and not str(layer).upper().startswith("L"):
        raise ValueError(f"layer must be L0..L7, got {layer!r}")
    ADAPTERS[ep] = {"ep": ep, "name": str(name), "where": str(where),
                    "fn": fn, "layer": (layer or "").upper()}


def load_slices(force: bool = False) -> Dict[str, str]:
    """Import every slice module so its `register` calls run.

    Returns {slice_name: "ok" | "MISSING: <why>"}. A slice that fails to import
    is reported, never fatal: the runtime must stay usable when one layer's
    optional dependency is absent.
    """
    global _loaded
    report: Dict[str, str] = {}
    if _loaded and not force:
        return report
    for name in SLICES:
        mod = f"CORE.technique_wiring.{name}"
        try:
            importlib.import_module(mod)
            report[name] = "ok"
        except Exception as exc:                      # pragma: no cover - env
            note_failure("technique_runtime:L122", exc)
            report[name] = f"MISSING: {type(exc).__name__}: {exc}"
    _loaded = True
    return report


def _ep_key(ep: str) -> tuple:
    """Sort EPs numerically ('EP9' before 'EP10'), non-numeric last."""
    try:
        return (0, int(ep[2:]))
    except ValueError as exc:
        note_failure("technique_runtime:L132", exc)
        return (1, 0)


def status(record: bool = True) -> Dict[str, Any]:
    """The registry as data: what is wired, where, and on which layer."""
    load_slices()
    rows = [{"ep": a["ep"], "name": a["name"], "where": a["where"],
             "layer": a["layer"]} for a in ADAPTERS.values()]
    rows.sort(key=lambda r: _ep_key(r["ep"]))
    out = {"n_wired": len(rows), "wirings": rows}
    if record:
        _cite("technique-wiring", True,
              [f"wired:{len(rows)}"], [])
    return out


def run_all(record: bool = True) -> Dict[str, Any]:
    """Invoke EVERY registered adapter. This is the point of the module.

    Returns {"n", "engaged", "errored", "results": {ep: {...}}}. `engaged` counts
    adapters that ran to completion; `errored` counts adapters that raised (a bug
    in the adapter, reported — never swallowed). A non-zero `errored` means this
    module's own contract is broken.
    """
    load_slices()
    results: Dict[str, Any] = {}
    engaged = errored = 0
    for ep in sorted(ADAPTERS, key=_ep_key):
        a = ADAPTERS[ep]
        t0 = time.perf_counter()
        try:
            value = a["fn"]()
            engaged += 1
            entry = {"ok": True, "where": a["where"],
                     "summary": _summarise(value),
                     "ms": round((time.perf_counter() - t0) * 1000, 2)}
            if isinstance(value, dict):
                entry["verdict"] = _verdict_keys(value)
        except Exception as exc:
            note_failure("technique_runtime:L177", exc)
            errored += 1
            entry = {"ok": False, "where": a["where"],
                     "error": f"{type(exc).__name__}: {exc}",
                     "ms": round((time.perf_counter() - t0) * 1000, 2)}
        results[ep] = entry

    out = {"n": len(results), "engaged": engaged, "errored": errored,
           "results": results}
    if record:
        _cite("technique-runtime", errored == 0,
              [f"wired:{len(results)}", f"engaged:{engaged}",
               f"errored:{errored}"],
              [f"fix adapter: {ep}" for ep, r in results.items()
               if not r["ok"]])
    return out


def _verdict_keys(value: Dict[str, Any]) -> Dict[str, Any]:
    """The boolean verdict flags an adapter's result carries, if any."""
    keys = ("accepted", "valid", "ok", "complete", "executable", "closed",
            "permitted", "passed", "engaged")
    return {k: value[k] for k in keys if isinstance(value.get(k), bool)}


def _summarise(value: Any) -> str:
    """A one-line, bounded description of an adapter's return value."""
    if value is None:
        return "None"
    if isinstance(value, dict):
        return ", ".join(f"{k}={_short(v)}" for k, v in list(value.items())[:6])
    if isinstance(value, (list, tuple, set)):
        return f"{type(value).__name__}({len(value)})"
    return _short(value)


def _short(v: Any, limit: int = 40) -> str:
    s = str(v)
    return s if len(s) <= limit else s[:limit - 1] + "\u2026"


# -- direct helpers: fail-open boundary entry points ---------------------------
#
# These are the two entry points real code calls (verify_boundary,
# reflect_on_failure). Each is fail-open and each records a ledger citation, so
# an integration is auditable at the boundary rather than only in self-tests.

def verify_boundary(subject: str, **kwargs: Any) -> Dict[str, Any]:
    """Verify a layer transition before accepting it (verify-before-accept).

    Thin, fail-open wrapper over `verification_gate.verify_before_accept` — the
    function that nine composed detectors sit behind and that, before this
    runtime, no production boundary called.

    Extra kwargs are forwarded when the gate's signature accepts them and dropped
    otherwise, so a caller written against an older gate cannot crash a boundary.
    """
    try:
        from CORE import verification_gate as vg
        sig = getattr(vg, "verify_before_accept", None)
        if sig is None:
            out = {"accepted": True, "reasons": ["no-gate"],
                   "remediations": [], "citation": ""}
        else:
            import inspect
            params = inspect.signature(sig).parameters
            accepted = {k: v for k, v in kwargs.items() if k in params}
            verdict = sig(subject=str(subject), **accepted)
            out = verdict if isinstance(verdict, dict) else {
                "accepted": bool(verdict), "reasons": [], "remediations": [],
                "citation": ""}
    except Exception as exc:
        # A verifier that fails must not block the boundary it guards: report
        # the failure and let the caller proceed (fail-open is the house rule
        # for every gate in this tree) -- but RECORD it, so a gate that is
        # silently erroring is visible in failure_notes() rather than looking
        # exactly like a gate that passed.
        record_link("technique_runtime.verify_boundary", subject, False,
                    citation=str(exc) or "")
        note_failure("verify-boundary", exc)
        out = {"accepted": True, "reasons": [f"gate-error:{type(exc).__name__}"],
               "remediations": [], "citation": ""}
    record_link("technique_runtime.verify_boundary", subject,
                bool(out.get("accepted", True)),
                citation=str(out.get("citation", "")))
    return out


def reflect_on_failure(question: str, what_failed: str, what_next: str,
                       decision_id: str | None = None,
                       predicted: float | None = None) -> Dict[str, Any]:
    """Write one verbal reflection after a failed attempt (Reflexion, EP113).

    Adds what the buffer was missing: `predicted`, the caller's own confidence
    *before* the attempt, stored with the realised outcome so calibration can be
    measured later instead of asserted (EP294).
    """
    written = False
    try:
        from CORE.WORMS.expansion import reflexion as _rx
        written = bool(_rx.record_reflection(
            question=question, what_failed=what_failed, what_next=what_next,
            decision_id=decision_id))
        if predicted is not None:
            # Forensic alignment fix: this function runs only after a failed
            # attempt, so the realised outcome is measured right here — the
            # attempt failed (0.0). Storing predicted alone left every row
            # unscored downstream; storing 0.0 is the observation, not a guess.
            _record_calibration(question, predicted, decision_id,
                                realised=0.0)
    except Exception as exc:
        record_link("technique_runtime.reflect_on_failure", question, False,
                    citation=str(exc) or "")
        note_failure("technique_runtime:L275", exc)
        written = False
    record_link("technique_runtime.reflect_on_failure", question, written,
                decision_id=str(decision_id or ""))
    return {"written": written, "question": _short(question),
            "predicted": predicted}


def revise_until_confirmed(candidate: Any, subject: str, repair_fn: Callable,
                           max_depth: int = 3,
                           **gate_kwargs: Any) -> Dict[str, Any]:
    """Bounded generate-critique-revise loop DRIVEN BY the acceptance gate (EP370).

    `verification_gate.confirmed_revise` had no caller anywhere in the tree: the
    gate could REJECT a candidate and nothing could act on the rejection, so a
    refusal ended the attempt rather than starting a repair. This wires the two
    ends together -- the critique IS the gate's own rejection reasons, the
    revision is the caller's `repair_fn(candidate, critique)`, and the loop stops
    the moment the gate accepts, which is what bounds the cost.

    Fail-open like every other boundary here: an error returns the original
    candidate with `confirmed=False` rather than raising into a caller's path.
    """
    if _vg is None or not callable(getattr(_vg, "confirmed_revise", None)):
        return {"final": candidate, "depths_used": 0, "confirmed": False,
                "stopped_early": False, "reason": "no-gate"}

    def _critique(c: Any) -> Any:
        verdict = verify_boundary(subject, **gate_kwargs)
        if verdict.get("accepted"):
            return None                      # CONFIRMED -> confirmed_revise stops
        reasons = [str(r) for r in (verdict.get("reasons") or [])]
        return "; ".join(reasons) or "rejected"

    def _revise(c: Any, critique: str) -> Any:
        return repair_fn(c, critique)

    try:
        out = _vg.confirmed_revise(candidate, _critique, _revise,
                                   max_depth=max_depth)
        out = dict(out or {})
    except Exception as exc:
        record_link("technique_runtime.revise_until_confirmed", subject, False,
                    citation=str(exc) or "")
        note_failure("technique_runtime:revise-until-confirmed", exc)
        return {"final": candidate, "depths_used": 0, "confirmed": False,
                "stopped_early": False, "reason": f"{type(exc).__name__}"}
    record_link("technique_runtime.revise_until_confirmed", subject,
                bool(out.get("confirmed")),
                citation=str(out.get("reason", "")))
    return out


def recall_with_feedback(query: str, consolidate: bool = False,
                         **kwargs: Any) -> Dict[str, Any]:
    """Recall through the router and feed the read statistics back (EP235).

    RESTORED. The theater removal deleted this function because it had "zero
    `src/` callers" -- true only because nothing had wired it yet, and it was the
    consumer that made the router's read statistics mean anything. Removing the
    only consumer of a signal while the producer keeps emitting it is what left
    the read path looking like it worked while nothing learned from it.

    `consolidate=True` also closes the loop: the derived read pressure is handed
    to `engram.run_read_driven_consolidation`, so a recall can act on what the
    previous reads reported missing. Default off so a caller that only wants the
    route never pays for a consolidation pass.
    """
    if _engram is None:
        return {"routed": False, "reason": "no-engram"}
    try:
        router_cls = getattr(_engram, "RecallRouterWorm", None)
        if router_cls is None:
            return {"routed": False, "reason": "no-router"}
        router = router_cls()
        route = router.route_recall(str(query))
        state = {}
        try:
            state = router.recall_state() or {}
        except Exception as exc:
            note_failure("technique_runtime:382", exc)
            state = {}
        out: Dict[str, Any] = {
            "routed": True,
            "route": (route or {}).get("route"),
            "mode": (route or {}).get("mode"),
            "total_reads": state.get("total"),
            "abstained": state.get("abstained"),
            # EP235 residual: the scores were already IN HAND. `recall_state()`
            # returns `last_scores` alongside total/abstained
            # (CORE/WORMS/expansion/engram.py:544), so the recorded cause
            # ("never reads stats()") was wrong -- the data was fetched and then
            # dropped here, which is why every consumer payload was permanently
            # empty. Surface it instead of taking a second reading.
            "last_scores": list(state.get("last_scores") or []),
        }
        # Bound by the module-level `from ... import` above, NOT resolved as a
        # string. A `getattr(mod, "name")` is invisible to static reachability
        # analysis -- the exact defect class the mesh wire-or-cut gate exists to
        # catch, where `PF16` reports the callee as having "no production
        # reference of any kind". An `X as _Y` alias is equally invisible, so the
        # name is bound bare: the bound name IS the reference.
        if consolidate and run_read_driven_consolidation is not None:
            closed = run_read_driven_consolidation(router=router)
            out["consolidated"] = bool(closed.get("read_driven"))
            out["needs_writing"] = bool(
                (closed.get("read_pressure") or {}).get("needs_writing"))
        return out
    except Exception as exc:
        note_failure("technique_runtime:recall-with-feedback", exc)
        return {"routed": False, "reason": f"{type(exc).__name__}"}


def _record_calibration(question: str, predicted: float,
                        decision_id: str | None,
                        realised: float | None = None) -> None:
    """Store a predicted-vs-realised row (EP294). Fail-open.

    `realised` travels only when the caller measured it — never invented.
    Omit it (None) and the row counts as unscored downstream instead of
    carrying a guess.
    """
    try:
        from CORE.local_memory import get_memory_store
        data = {"question": _short(question, 200),
                "predicted": float(predicted),
                "decision_id": str(decision_id or "")}
        if realised is not None:
            data["realised"] = float(realised)
        get_memory_store().record_event(
            source="reflection_calibration",
            event_type="reflection_calibration",
            data=data,
            severity="info", tags=["reflection", "calibration"])
    except Exception as exc:
        note_failure("reflection-calibration", exc)


def _cite(subject: str, accepted: bool, reasons: List[str],
          remediations: List[str]) -> str:
    """Cite a runtime verdict on the hash-chained ledger (fails open).

    NOT write-only, despite ACTION_REGISTRY D-2 listing the `technique-wiring`
    citations (this file's `status()` and `run_all()` at the two cited sites) as
    emit-only. The citation lands on the hash-chained ledger and the ledger is
    read by the compliance path: `audit_trail.verify_completeness` is called from
    `compliance/sentinel_gate.py`, `cli_audit.py`, `compliance/governance.py`,
    `audit_repair.py` and `scripts/close_audit_sessions.py`, and the sibling
    `technique-link` join rows are read by `structured_action.authority_ledger`,
    `training_intake`, `scripts/live_monitor.py` and `fleet_link_health()`.
    Removing the emit would therefore subtract from the audit trail, so the D-2
    verdict here is: keep the emit, and the reader chain is recorded in this
    docstring rather than rediscovered on the next audit.
    """
    try:
        from CORE import audit_trail as _at
        return _at.audit_verification_gate(subject, accepted,
                                           list(reasons), list(remediations))
    except Exception as exc:
        # Uncited, but recorded: a missing ledger is a degraded audit trail, and
        # `failure_notes()` must be able to say so.
        note_failure("audit-cite", exc)
        return ""


def _producer_identity() -> str:
    """Caller module identity for record_link rows (per-worm hook identity).

    Walks past this module's own frames to the first external caller and
    returns its `__name__` (e.g. `CORE.WORMS.expansion.queens`). Lets the
    intake attribute a hook row to the worm that produced it instead of
    guessing from the hook string. Stdlib-only, never raises: "unknown"
    on any failure so the row is still written.
    """
    try:
        for frame_info in inspect.stack()[1:]:
            mod = str(frame_info.frame.f_globals.get("__name__", "") or "")
            if mod and mod != __name__ and not mod.startswith("importlib"):
                return mod
    except Exception as exc:
        note_failure("technique-runtime:producer-identity", exc)
    return "unknown"


def record_link(hook: str, subject: str, ok: bool,
                citation: str = "", decision_id: str = "",
                worm: str = "") -> bool:
    """Write one join row for a live hook invocation (memory unity, Phase 1).

    Every production hook call leaves a row in the canonical store
    (LocalMemoryStore) keyed by hook+subject, so ledger citations, memory
    recall, and future training intake can JOIN on it instead of living in
    separate systems. source="technique-link".
    Fail-open: never raises; False only if the store is unwritable.

    Per-worm hook identity: `worm` names the producing worm/module. When
    empty it is derived from the caller module, so every row carries an
    attributable producer without call-site churn. Additive only: existing
    readers that ignore the field see byte-identical verdicts.
    """
    producer = str(worm or "") or _producer_identity()
    kept = False
    try:
        kept = bool(get_memory_store().record_event(
            "technique-link",
            "hook:%s" % hook,
            {"subject": str(subject), "ok": bool(ok),
             "citation": str(citation or ""),
             "decision_id": str(decision_id or ""),
             "worm": producer},
            severity="info" if ok else "warning",
            tags=["technique-link", str(hook), producer]))
    except Exception as exc:
        note_failure("technique-runtime:record-link", exc)
    return kept


def _read_pressure_summary(limit: int = 100) -> Dict[str, Any]:
    """The READ side for the read-pressure rows the engram writer emits.

    ACTION_REGISTRY D-2 recorded `engram_read_pressure` as write-only ("1 hit
    repo-wide"). The premise was true: `DreamConsolidatorWorm._record_read_stats`
    persisted rows with `source="engram-read-pressure"` and nothing queried them.
    The fix is a consumer, not a deletion, because the persisted series is the
    only durable record of unmet demand across process runs -- the derived dict
    that `read_pressure()` returns dies with the run.

    This reader is reached from production: `scripts/technique_heartbeat.py` calls
    `fleet_link_health()`, which folds this summary in, and `scripts/live_monitor.py`
    reads the same store by source. Nothing new calls it and no caller was invented.

    `sustained_unmet` is deliberately not a single sample: the writer already emits
    only when its own floor was crossed, so >=2 of the newest 3 unmet signals is the
    honest bar for "the store is persistently missing what consumers ask for".

    Fail-open: the empty summary on any error. It reports; it never gates.
    """
    out: Dict[str, Any] = {"n": 0, "latest_signal": None,
                           "abstention_rate": None, "sustained_unmet": False}
    try:
        rows = get_memory_store().query_memories(
            source="engram-read-pressure", limit=int(limit)) or []
    except Exception as exc:
        note_failure("technique-runtime:read-pressure-read", exc)
        return out
    if not isinstance(rows, list) or not rows:
        return out
    signals: List[str] = []
    rates: List[float] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        raw_data: Any = r.get("data")
        data: dict = raw_data if isinstance(raw_data, dict) else {}
        if data.get("signal"):
            signals.append(str(data["signal"]))
        rate = data.get("abstention_rate")
        if isinstance(rate, (int, float)):
            rates.append(float(rate))
    out["n"] = len(rows)
    out["latest_signal"] = signals[0] if signals else None
    out["abstention_rate"] = round(sum(rates) / len(rates), 4) if rates else None
    out["sustained_unmet"] = signals[:3].count("unmet-demand") >= 2
    return out


def fleet_link_health(limit: int = 100) -> Dict[str, Any]:
    """Fleet hook-health rate over recent technique-link rows (memory unity).

    Pure read of the canonical store: mean ok-rate per hook plus totals, and the
    read-pressure summary folded in (ACTION_REGISTRY D-2) so the engram router's
    persisted pressure rows have a reader on this production path.
    Returns {"n", "ok_rate" (None when empty), "by_hook": {hook: rate},
    "read_pressure": {"n", "latest_signal", "abstention_rate",
    "sustained_unmet"}}.
    Fail-open: {} on any error. Powers monitors and, later, evolution
    fitness terms — it reports, it never gates.
    """
    out: Dict[str, Any] = {"n": 0, "ok_rate": None, "by_hook": {}}
    rows: List[Dict[str, Any]] = []
    try:
        rows = get_memory_store().query_memories(
            source="technique-link", limit=int(limit)) or []
    except Exception as exc:
        # Fail-open: anything below degrades to the empty result above, and
        # the miss itself is recorded instead of vanishing.
        note_failure("technique-runtime:link-health-read", exc)
    if not isinstance(rows, list):
        rows = []
    trues = sum(
        1 for r in rows
        if isinstance(r.get("data"), dict) and r["data"].get("ok") is True)
    by_hook: Dict[str, Dict[str, int]] = {}
    for r in rows:
        raw_data: Any = r.get("data")
        data: dict = raw_data if isinstance(raw_data, dict) else {}
        hook = str((r.get("event_type") or "").replace("hook:", "")
                   or data.get("hook", "unknown"))
        cell = by_hook.setdefault(hook, {"n": 0, "ok": 0})
        cell["n"] += 1
        if data.get("ok") is True:
            cell["ok"] += 1
    out["n"] = len(rows)
    out["ok_rate"] = (trues / len(rows)) if rows else None
    #: Second store signal, same call (ACTION_REGISTRY D-2): the read-pressure
    #: rows the engram router writes now have a reader on this production path.
    out["read_pressure"] = _read_pressure_summary(limit=limit)
    out["by_hook"] = {h: (c["ok"] / c["n"] if c["n"] else None)
                      for h, c in by_hook.items()}
    return out
