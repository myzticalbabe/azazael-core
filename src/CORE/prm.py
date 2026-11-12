# coding: utf-8
"""prm.py — process reward modeling: SP-PRM induction + R-PRM critiques.

SP-PRM: derive step rewards from an outcome model by scoring prefixes
(dual-consistency without step labels). R-PRM: generative verification
traces (analysis + Yes/No) with a local-model hook and a heuristic
fallback so it runs CPU-only today.
"""
import math
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def sp_prm_scores(orm_score_fn, tokens: list[str]) -> list[float]:
    """Score every prefix with the ORM; deltas are implicit step rewards.

    orm_score_fn(prefix_text) -> float in [0,1]. Returns per-step deltas
    (FreePRM/PRIME-style: prefix log-odds as the free process signal).
    """
    full, cum = [], 0.0
    for i in range(1, len(tokens) + 1):
        try:
            p = max(1e-6, min(1 - 1e-6, float(orm_score_fn(" ".join(tokens[:i])))))
        except Exception as exc:
            note_failure("prm:L22", exc)
            p = 0.5
        s = math.log(p / (1.0 - p))
        full.append(s - cum)
        cum = s
    return full


def _heuristic_critique(step: str) -> tuple[str, float]:
    """Fallback verifier: flags hedging/contradiction markers."""
    low = step.lower()
    flags = ("maybe", "possibly", "not sure", "however", "but ",
             "contradiction", "ignore previous")
    hit = sum(1 for f in flags if f in low)
    ok = hit == 0 and len(step.split()) > 2
    analysis = ("no hedging markers; substantive step" if ok
                else f"{hit} hedging/contradiction marker(s)")
    return analysis, (0.85 if ok else 0.25)


def r_prm_verify(steps: list[str], judge_fn=None) -> list[dict]:
    """Reasoning-driven process verification, one record per step.

    judge_fn(step) -> (analysis, score). Default: heuristic fallback.
    Returns [{step, analysis, score}].
    """
    judge_fn = judge_fn or _heuristic_critique
    out = []
    for s in steps:
        try:
            analysis, score = judge_fn(s)
        except Exception as e:
            note_failure("prm:L53", e)
            analysis, score = f"judge-fail: {e}"[:800], 0.5
        out.append({"step": s[:200], "analysis": analysis,
                    "score": round(max(0.0, min(1.0, float(score))), 3)})
    return out
