# coding: utf-8
"""ttcompute.py — test-time compute bandit: route EFFORT, not just models.

Arms: direct (1x), think (2x), bestofN (Nx), escalate (triage+big).
Reward = correctness - lam * cost. Thompson over Beta per arm, cost-aware
priors (cheap arms start optimistic). Stdlib-only.
"""
import random
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


COST = {"direct": 1.0, "think": 2.0, "bestof4": 4.0, "escalate": 6.0}


class ComputeBandit:
    VERSION = "1.0.0"

    def __init__(self, lam: float = 0.05, seed: int = 0):
        self.lam = float(lam)
        self._rng = random.Random(seed)
        # cheap arms start optimistic: pseudo-successs ~ 1/cost
        self._s = {a: 1.0 / c for a, c in COST.items()}
        self._f = {a: 1.0 for a in COST}

    def pick(self) -> str:
        draws = {a: self._rng.betavariate(max(1e-3, self._s[a]),
                                          max(1e-3, self._f[a]))
                 for a in COST}
        return max(draws, key=lambda a: draws[a])

    def update(self, arm: str, correct: bool, cost_mult: float = 1.0):
        """correct: did the answer verify. cost_mult: actual/expected cost."""
        r = (1.0 if correct else 0.0) - self.lam * COST[arm] * cost_mult
        if r > 0:
            self._s[arm] += r
        else:
            self._f[arm] += -r

    def stats(self) -> dict:
        return {a: {"w": round(self._s[a] / (self._s[a] + self._f[a]), 3)}
                for a in COST}
