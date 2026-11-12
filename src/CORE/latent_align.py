#!/usr/bin/env python3
"""latent_align.py — Training-free latent comms (blueprint L7, latent slice).

Implements the StateBridge mechanics (EP440, 2608.13317) that survive
without a model in the loop:

- norm calibration: L2-normalize carriers before injection (pure stdlib);
- closed-form orthogonal alignment: Procrustes rotation mapping one latent
  space onto another (torch-gated — names "torch-unavailable" when the
  backend is absent instead of silently degrading);
- continuous-prefix injection: prepend continuous prefix vectors to a
  hidden sequence (pure stdlib).

NOT covered here: LRT's recurrent refinement loop (needs a live reasoner)
— explicitly out of scope, named in gaps when asked for. Latent verdicts
ride the existing `verification_gate` ledger entry (subject
"latent:<name>") — no new ledger type.
"""

from typing import Any, Dict, List
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


# -- Norm calibration (pure stdlib) ---------------------------------------------------
def norm_calibrate(vec: List[float]) -> Dict[str, Any]:
    """L2-normalize a latent carrier (StateBridge calibration).

    Returns {"calibrated", "norm" (pre-calibration L2), "gaps"}. Zero
    vectors are uncalibratable: gap "zero-vector", calibrated [].
    """
    try:
        vals = [float(x) for x in (vec or [])]
    except (TypeError, ValueError) as exc:
        note_failure("latent_align:L53", exc)
        return {"calibrated": [], "norm": 0.0, "gaps": ["non-numeric-vector"]}
    norm = math.sqrt(sum(v * v for v in vals))
    if not vals:
        return {"calibrated": [], "norm": 0.0, "gaps": ["empty-vector"]}
    if norm == 0.0:
        return {"calibrated": [], "norm": 0.0, "gaps": ["zero-vector"]}
    return {"calibrated": [v / norm for v in vals],
            "norm": norm, "gaps": []}


# -- Closed-form orthogonal alignment (torch-gated) --------------------------------------
def orthogonal_align(source: List[List[float]],
                     target: List[List[float]]) -> Dict[str, Any]:
    """Map source latent rows onto target via Procrustes rotation.

    source/target: row-major matrices (n_rows x dim). Returns {"aligned"
    (source rows rotated), "rotation" (dim x dim orthogonal R), "gaps"}.
    Gap "torch-unavailable" when no backend; shape problems named
    ("ragged-matrix", "dim-mismatch:<s>vs<t>", "empty-matrix").
    """
    def _check(m: Any) -> List[List[float]]:
        rows = [[float(x) for x in r] for r in (m or [])]
        if not rows:
            raise ValueError("empty-matrix")
        if any(len(r) != len(rows[0]) for r in rows):
            raise ValueError("ragged-matrix")
        return rows

    try:
        S, T = _check(source), _check(target)
    except ValueError as e:
        note_failure("latent_align:L84", e)
        return {"aligned": None, "rotation": None, "gaps": [str(e)]}
    if len(S[0]) != len(T[0]):
        return {"aligned": None, "rotation": None,
                "gaps": [f"dim-mismatch:{len(S[0])}vs{len(T[0])}"]}
    try:
        import torch  # noqa: PLC0415 (optional backend, gated)
    except ImportError as exc:
        note_failure("latent_align:L91", exc)
        return {"aligned": None, "rotation": None,
                "gaps": ["torch-unavailable"]}
    St = torch.tensor(S, dtype=torch.float64)
    Tt = torch.tensor(T, dtype=torch.float64)
    M = Tt.T @ St
    U, _, Vt = torch.linalg.svd(M)
    R = (Vt.T @ U.T).tolist()
    R = [[float(x) for x in row] for row in R]
    aligned = (St @ torch.tensor(R, dtype=torch.float64)).tolist()
    return {"aligned": [[float(x) for x in row] for row in aligned],
            "rotation": R, "gaps": []}


# -- Continuous-prefix injection (pure stdlib) ----------------------------------------------
def prefix_inject(prefix: List[List[float]],
                  hidden: List[List[float]]) -> Dict[str, Any]:
    """Prepend continuous prefix rows to a hidden sequence (StateBridge).

    Returns {"sequence" (prefix + hidden), "n_prefix", "gaps"} — gap
    "empty-prefix" when there is nothing to inject (a no-op, named).
    """
    prefix = [list(map(float, r)) for r in (prefix or [])]
    hidden = [list(map(float, r)) for r in (hidden or [])]
    gaps = [] if prefix else ["empty-prefix"]
    return {"sequence": prefix + hidden, "n_prefix": len(prefix),
            "gaps": gaps}


# -- Ledger citation ----------------------------------------------------------------------------
def record_latent(name: str, result: Dict[str, Any]) -> str:
    """Cite a latent verdict on the hash-chained ledger (fails open)."""
    result = result or {}
    gaps = list(result.get("gaps", []) or [])
    citation = ""
    try:
        from CORE import audit_trail as _at  # noqa: PLC0415 (lazy: no cycle)
        citation = _at.audit_verification_gate(
            f"latent:{str(name or '?').strip()}", not gaps,
            [f"latent-{('ok' if not gaps else 'gaps')}:{len(gaps)}"],
            [f"repair latent gap: {g}" for g in gaps])
    except Exception as exc:                          # noqa: BLE001 - ledger best-effort
        _note_failure("latent_align:ledger-cite", exc)
    return citation
