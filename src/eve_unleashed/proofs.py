"""Funnel integrity proofs: a funnel that can't show its inputs is narrative.

Every verdict in the tree is a funnel (many edges in, one point out).
A proof binds the verdict to its inputs: each input's name and digest,
plus the reduction rule in words. `verify_funnel` replays it against
live artifacts -- PASS only when every input is present and unchanged.
"""

from __future__ import annotations

from typing import Dict


def funnel_proof(inputs: Dict[str, str], rule: str) -> Dict:
    """Seal a verdict: {input name: digest} + the reduction rule."""
    if not inputs:
        raise ValueError("a funnel with no inputs proves nothing")
    if not rule:
        raise ValueError("a funnel with no stated rule proves nothing")
    return {"inputs": dict(inputs), "rule": rule,
            "n_inputs": len(inputs)}


def verify_funnel(proof: Dict, artifacts: Dict[str, str]) -> Dict:
    """Replay the seal against live artifacts. Digests decide, not prose."""
    want = proof.get("inputs", {})
    missing = sorted(k for k in want if k not in artifacts)
    mismatched = sorted(k for k in want
                        if k in artifacts and artifacts[k] != want[k])
    ok = not missing and not mismatched
    return {"verdict": "PASS" if ok else "FAIL", "clean": True,
            "n_inputs": len(want), "missing": missing,
            "mismatched": mismatched, "rule": proof.get("rule", "")}
