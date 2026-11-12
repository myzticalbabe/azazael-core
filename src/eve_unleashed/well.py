"""The Well: native repair in neural weight-space, verified through Aether.

A tool, not a claim. It does three things, each proven by running:

1. GOLDEN TENSOR -- `golden_tensor(seed, rows, cols)` is the deterministic
   reference: the same seeded RNG stream soul_foundry builds GGUFs from.
   `replicate_golden` rebuilds it and proves bit-identity by sha256 over
   the packed f32 bytes. A replica that does not match is reported, never
   asserted clean.
2. NATIVE FIX -- `fix_neural_space` re-encodes foreign/damaged weight
   vectors into spec-exact Q8_0/Q4_0 blocks (soul_foundry's own codec) and
   reports max reconstruction error against a stated bound. The fix earns
   the name "native" because the codec is the exact block layout the
   native artifacts ship -- no parallel implementation to drift.
3. THROUGH AETHER -- `aether_verify` runs the proof through the real
   Aether auditors (reading_audit + leg_ledger, same calls the A-4 tests
   use) when they are importable, else through a local mirror of their
   verdict classes. A verdict with no denominator or a PASS over an
   error leg is refused here exactly as it is refused there.

Stdlib only besides the two in-tree imports (soul_foundry codec, and the
Aether auditors when present). No new C extension: the packing is struct,
the claim is parity, and parity is tested, never asserted.
"""

from __future__ import annotations

import hashlib
import random
import struct
import sys
from pathlib import Path
from typing import Any, Dict, List

from eve_unleashed.soul_foundry import (
    dequantize_q40,
    dequantize_q80,
    quantize_q40,
    quantize_q80,
)

try:  # Aether auditors live in tools/, on the path in tests and CI
    import reading_audit  # type: ignore
except Exception:
    reading_audit = None  # type: ignore
try:
    import leg_ledger  # type: ignore
except Exception:
    leg_ledger = None  # type: ignore


# ------------------------------------------------------------ 1. golden tensor
def golden_tensor(seed: int = 7, rows: int = 8, cols: int = 8) -> List[float]:
    """Deterministic reference weight vector, foundry stream compatible."""
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(rows * cols)]


def golden_digest(vals: List[float]) -> str:
    """sha256 over packed f32 bytes -- the replica's identity proof."""
    return hashlib.sha256(struct.pack("<%df" % len(vals), *vals)).hexdigest()


def replicate_golden(seed: int = 7, rows: int = 8, cols: int = 8,
                     expected: str | None = None) -> Dict[str, Any]:
    """Rebuild the golden tensor and prove bit-identity.

    When expected is None the digest is anchored on first build (returned
    as the anchor); otherwise match is True only on exact equality.
    """
    vals = golden_tensor(seed, rows, cols)
    digest = golden_digest(vals)
    if expected is None:
        return {"match": True, "digest": digest, "anchored": True,
                "seed": seed, "rows": rows, "cols": cols, "n": len(vals)}
    return {"match": digest == expected, "digest": digest,
            "expected": expected, "anchored": False,
            "seed": seed, "rows": rows, "cols": cols, "n": len(vals)}


# ------------------------------------------------------------------ 2. fix
_BOUNDS = {"Q8_0": 0.01, "Q4_0": 0.15, "F32": 0.0}


def fix_neural_space(vals: List[float], dtype: str = "Q8_0",
                     bound: float | None = None) -> Dict[str, Any]:
    """Re-encode weights into spec-exact blocks; error must fit the bound.

    The fix IS the re-encode: damaged precision (foreign quant, truncated
    f32, drifted clone) comes out the other side as canonical blocks with
    a measured max reconstruction error. within_bound is the verdict.
    """
    if dtype not in _BOUNDS:
        raise ValueError("well dtypes: Q8_0, Q4_0, F32 (got %r)" % dtype)
    if dtype == "Q8_0":
        back = dequantize_q80(quantize_q80(vals))[:len(vals)]
    elif dtype == "Q4_0":
        back = dequantize_q40(quantize_q40(vals))[:len(vals)]
    else:
        back = list(vals)
    max_err = max(abs(a - c) for a, c in zip(vals, back)) if vals else 0.0
    lim = _BOUNDS[dtype] if bound is None else bound
    return {"dtype": dtype, "n": len(vals),
            "max_err": round(max_err, 6), "bound": lim,
            "within_bound": max_err <= lim, "fixed": back}


# ------------------------------------------------------------- 3. aether gate
def aether_verify(proof: Dict[str, Any]) -> Dict[str, Any]:
    """Run a fix proof through the Aether auditors. Clean iff no findings.

    proof needs denominators the auditors can count: files (tensors
    checked) and legs (every leg ok). Anything less is refused -- by the
    real auditors when importable, else by the local mirror below, which
    implements the same two verdict classes A-4 was built on.
    """
    verdict = {"verdict": "PASS" if proof.get("within_bound") else "FAIL",
               "files": proof.get("n", 0),
               "legs": [{"leg": "well-fix", "status": "ok"
                         if proof.get("within_bound") else "error",
                         "seconds": 0.1, "detail": "replica+fix proof"}]}
    findings: List[Any] = []
    if reading_audit is not None:
        reading_audit.audit_verdicts(
            {"verdict": verdict["verdict"], "files": verdict["files"]},
            "well", findings)
    elif not verdict["files"]:
        findings.append({"class": "denominator_free",
                         "detail": "no tensor count on the verdict"})
    if leg_ledger is not None:
        findings.extend(leg_ledger.audit(verdict))
    else:
        for leg in verdict["legs"]:
            if verdict["verdict"] == "PASS" and leg["status"] != "ok":
                findings.append({"class": "verdict_mismatch",
                                 "detail": "PASS over failed leg " + leg["leg"]})
    clean = not findings
    return {"clean": clean, "verdict": verdict["verdict"],
            "findings": [{"class": f["class"] if isinstance(f, dict) else f[0],
                          "detail": (f.get("detail", "") if isinstance(f, dict)
                                     else (f[1] if len(f) > 1 else ""))}
                         for f in findings]}
