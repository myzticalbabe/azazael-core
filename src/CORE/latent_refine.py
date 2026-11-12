#!/usr/bin/env python3
"""latent_refine.py — Bounded residual refinement (LRT mechanics slice).

Implements the implementable half of the LRT steal (EP472, 2609.01117):
reasoning depth decoupled from size via a tiny recurrent loop applying
BOUNDED residual latent corrections. What lives here:

- bounded_correct: clamp a correction to an L2 bound, then apply it
  (the bound is the whole trick — corrections can never blow up the
  carrier, so iterating is safe);
- refine: run a correction sequence, tracking residual norms; reports
  whether residuals contracted (depth did work) with a fixed-size,
  parameter-free loop (depth decoupled from size).

NOT covered: the frozen-LLM + learned-reasoner pairing (needs a live
model) — explicitly out of scope. Corrections here are caller-supplied;
this module owns the bounding, the iteration, and the verdict. Verdicts
ride the existing `verification_gate` ledger entry (subject "lrt:<name>").
"""

from typing import Any, Callable, Dict, List
import math

#: Fail-open swallows recorded when the runtime recorder is unavailable. An
#: unrecorded swallow is indistinguishable from success, so every handler in
#: this module records first.
_SWALLOWED: List[str] = []


try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # runtime absent: stay importable
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        _SWALLOWED.append("%s:no-recorder" % where)


def _note_failure(where: str, exc: BaseException | None = None) -> None:
    """Record a fail-open swallow. Never raises."""
    try:
        note_failure(where, exc)
    except Exception as _e:
        _SWALLOWED.append("%s:%s" % (where, type(_e).__name__))


def _norm(vals: List[float]) -> float:
    return math.sqrt(sum(v * v for v in vals))


# -- Bounded residual correction --------------------------------------------------
def bounded_correct(state: List[float], correction: List[float],
                    bound: float) -> Dict[str, Any]:
    """Apply one L2-bounded residual correction (LRT mechanics).

    Scales correction to norm <= bound (no-op scale when already within),
    adds it to state. Returns {"state", "applied_norm", "scale",
    "gaps"} — gaps: "length-mismatch", "non-numeric", "non-positive-bound".
    """
    try:
        st = [float(x) for x in (state or [])]
        co = [float(x) for x in (correction or [])]
        bd = float(bound)
    except (TypeError, ValueError) as exc:
        note_failure("latent_refine:L62", exc)
        return {"state": [], "applied_norm": 0.0, "scale": 0.0,
                "gaps": ["non-numeric"]}
    if len(st) != len(co):
        return {"state": [], "applied_norm": 0.0, "scale": 0.0,
                "gaps": ["length-mismatch"]}
    if not (bd > 0.0):
        return {"state": [], "applied_norm": 0.0, "scale": 0.0,
                "gaps": ["non-positive-bound"]}
    n = _norm(co)
    scale = min(1.0, bd / n) if n > 0.0 else 0.0
    applied = [c * scale for c in co]
    return {"state": [s + a for s, a in zip(st, applied)],
            "applied_norm": _norm(applied), "scale": scale, "gaps": []}


# -- Depth-decoupled refinement loop ------------------------------------------------
def refine(initial: List[float],
           corrections: List[List[float]],
           bound: float) -> Dict[str, Any]:
    """Iterate bounded corrections, tracking residual contraction.

    corrections: caller-supplied per-step correction vectors (e.g. from a
    reasoner). Returns {"refined" (final state), "residuals" (per-step
    applied norms), "steps", "contracted" (residuals strictly decreasing
    and nonempty), "gaps"} — first gap encountered aborts the loop and is
    named (failures cite the step: "step-<i>:<gap>").
    """
    try:
        state = [float(x) for x in (initial or [])]
    except (TypeError, ValueError) as exc:
        note_failure("latent_refine:L92", exc)
        return {"refined": [], "residuals": [], "steps": 0,
                "contracted": False, "gaps": ["non-numeric-initial"]}
    residuals: List[float] = []
    gaps: List[str] = []
    for i, corr in enumerate(corrections or []):
        out = bounded_correct(state, corr, bound)
        if out["gaps"]:
            gaps.append(f"step-{i}:" + out["gaps"][0])
            break
        state = out["state"]
        residuals.append(out["applied_norm"])
    contracted = (len(residuals) >= 2
                  and all(b < a for a, b in zip(residuals, residuals[1:])))
    return {"refined": state, "residuals": residuals,
            "steps": len(residuals), "contracted": contracted, "gaps": gaps}


# -- Ledger citation -----------------------------------------------------------------------
def record_lrt(name: str, result: Dict[str, Any]) -> str:
    """Cite a refinement verdict on the hash-chained ledger (fails open)."""
    result = result or {}
    gaps = list(result.get("gaps", []) or [])
    citation = ""
    try:
        from CORE import audit_trail as _at  # noqa: PLC0415 (lazy: no cycle)
        citation = _at.audit_verification_gate(
            f"lrt:{str(name or '?').strip()}", not gaps,
            [f"lrt-{('ok' if not gaps else 'gaps')}:{result.get('steps', 0)}"
             f"-steps,contracted={bool(result.get('contracted'))}"],
            [f"repair refinement gap: {g}" for g in gaps])
    except Exception as exc:                          # noqa: BLE001 - ledger best-effort
        _note_failure("latent_refine:ledger-cite", exc)
    return citation
