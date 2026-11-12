#!/usr/bin/env python3
"""rubric_evolution.py — Rubric-grounded self-evolution (blueprint P2c, L6).

Implements the ARISE-RL mechanics (EP471, 2609.01058) as pure helpers:

- Generator side: ground rubric criteria in real tool observations
  (a criterion counts as grounded iff its keywords appear in observed
  tool output — intermediate-difficulty task selection stays caller-owned).
- Solver side: learn from FINE-GRAINED rubric satisfaction (per-criterion
  signals, not one brittle rollout reward).
- RG-SED: distill a memory-augmented variant back into the policy ONLY
  when the memory yields empirical reward improvement (strict gain gate —
  no blind imitation of noisy guidance).

Deliberately stdlib-only and LLM-free. Cycle verdicts ride the existing
`verification_gate` ledger entry (subject "rubric:<name>") — no new ledger
type. Helpers never train anything; they compute the gating signals
(remediate-don't-deny, PolicyGuide rule).
"""

from typing import Any, Dict, List

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


def _keywords(text: str) -> List[str]:
    return [w.strip(".,;:!?()[]{}\"'").lower() for w in str(text or "").split()
            if len(w.strip(".,;:!?()[]{}\"'")) >= 4]


# -- Generator: ground criteria in tool observations -----------------------------
def ground_criteria(criteria: List[str],
                    tool_observations: List[str]) -> Dict[str, Any]:
    """Mark each rubric criterion grounded/ungrounded (ARISE Generator).

    grounded iff >=1 of its keywords (len>=4) appears in the joined tool
    observations. Returns {"grounded": [{"criterion", "grounded"}],
    "n_grounded", "valid", "gaps"} — valid iff >=1 criterion grounded
    (an ungrounded rubric yields brittle rewards; gap names repair).
    """
    obs = " ".join(str(o or "") for o in (tool_observations or [])).lower()
    grounded = [{"criterion": str(c or ""),
                 "grounded": any(k in obs for k in _keywords(c or ""))}
                for c in (criteria or [])]
    n = sum(1 for g in grounded if g["grounded"])
    gaps = [] if n else ["no-grounded-criteria"]
    return {"grounded": grounded, "n_grounded": n,
            "valid": bool(n), "gaps": gaps}


# -- Solver: fine-grained satisfaction signals ------------------------------------
def score_attempt(grounded_rubric: Dict[str, Any],
                  satisfied: List[str]) -> Dict[str, Any]:
    """Score an attempt per grounded criterion (ARISE Solver signal).

    satisfied: criteria the attempt met. Ungrounded criteria are EXCLUDED
    (they carry no trustworthy signal). Returns {"score" (satisfied /
    grounded, 0.0 when none grounded), "n_grounded", "n_satisfied",
    "unstable"} — unstable iff zero grounded criteria (weak/noisy contrast).
    """
    grounded = [g["criterion"] for g in (grounded_rubric or {}).get("grounded", [])
                if g.get("grounded")]
    hits = set(str(s or "") for s in (satisfied or []))
    n_sat = sum(1 for c in grounded if c in hits)
    return {"score": (n_sat / len(grounded)) if grounded else 0.0,
            "n_grounded": len(grounded), "n_satisfied": n_sat,
            "unstable": not grounded}


# -- RG-SED: reward-gated distillation ----------------------------------------------
def gate_distillation(baseline_reward: float,
                      memory_reward: float) -> Dict[str, Any]:
    """Distill memory-augmented policy back ONLY on empirical gain (RG-SED).

    Returns {"distill" (strict memory_reward > baseline_reward), "gain"}.
    Equality or regression never distills — avoids blind imitation and
    distribution mismatch.
    """
    try:
        base = float(baseline_reward)
        mem = float(memory_reward)
    except (TypeError, ValueError) as exc:
        note_failure("rubric_evolution:L100", exc)
        return {"distill": False, "gain": 0.0,
                "gaps": ["non-numeric-rewards"]}
    return {"distill": mem > base, "gain": mem - base}


# -- Ledger citation ------------------------------------------------------------------
def record_rubric_cycle(name: str, result: Dict[str, Any]) -> str:
    """Cite a rubric-cycle verdict on the hash-chained ledger (fails open)."""
    result = result or {}
    accepted = bool(result.get("valid", result.get("distill", True)))
    gaps = list(result.get("gaps", []) or [])
    citation = ""
    try:
        from CORE import audit_trail as _at  # noqa: PLC0415 (lazy: no cycle)
        citation = _at.audit_verification_gate(
            f"rubric:{str(name or '?').strip()}", accepted,
            [f"rubric-{('ok' if accepted else 'gaps')}:{len(gaps)}"],
            [f"repair rubric gap: {g}" for g in gaps])
    except Exception as exc:                          # noqa: BLE001 - ledger best-effort
        _note_failure("rubric_evolution:ledger-cite", exc)
    return citation
