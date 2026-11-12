# coding: utf-8
"""redteam.py — reward red-team critic: predicts which high-reward
trajectories are hacks BEFORE they pay out.

Features are hack signatures, not outcomes: verbosity ratio, template
regurgitation (repetition), confidence/outcome mismatch history, and
score velocity spikes. A trajectory the task reward loves but the critic
flags gets its reward taxed: r' = r * (1 - hack_p). Stdlib-only.
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


def featurize(traj: dict) -> list[float]:
    """traj: {tokens, distinct_tokens, template_match(0..1), conf,
    outcome_ok(0/1 or None), score_vel}. Returns fixed feature vector."""
    toks = max(1, int(traj.get("tokens", 1)))
    distinct = max(1, int(traj.get("distinct_tokens", toks)))
    return [
        min(2.0, toks / 500.0),                      # verbosity
        1.0 - distinct / toks,                        # repetition
        float(traj.get("template_match", 0.0)),       # regurgitation
        float(traj.get("conf", 0.5)),                 # claimed confidence
        min(3.0, abs(float(traj.get("score_vel", 0.0)))),  # spike
    ]


class HackCritic:
    """Online logistic critic over hack signatures. Labels come from
    adjudication: 1 = confirmed hack (fluent-but-wrong, verbose filler),
    0 = legit. Starts as a prior; every adjudication sharpens it."""

    VERSION = "1.0.0"

    def __init__(self, dim: int = 5, lr: float = 0.5):
        self.w = [0.0] * dim
        self.b = -1.0  # prior: innocent until proven guilty
        self.lr = lr
        self.n = 0

    def hack_p(self, traj: dict) -> float:
        x = featurize(traj)
        z = self.b + sum(wi * xi for wi, xi in zip(self.w, x))
        return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))

    def adjudicate(self, traj: dict, is_hack: bool):
        """Online SGD step on a confirmed label."""
        x = featurize(traj)
        p = self.hack_p(traj)
        y = 1.0 if is_hack else 0.0
        err = p - y
        self.w = [wi - self.lr * err * xi for wi, xi in zip(self.w, x)]
        self.b -= self.lr * err
        self.n += 1

    def score_dimensions(self, answers: dict[str, bool]) -> dict:
        """Adversarial review of a surface, scored per design dimension.

        Delegates to the module-level scorer so a caller that already holds a
        trained critic has the checklist on the same object.
        """
        return score_dimensions(answers)

    def taxed_reward(self, reward: float, traj: dict) -> dict:
        """r' = r * (1 - hack_p). Never flips sign, only taxes."""
        p = self.hack_p(traj)
        return {"reward": reward, "hack_p": round(p, 3),
                "taxed": round(reward * (1.0 - p), 4)}


def progras_center(scores: list[float], outcomes: list[int]) -> list[float]:
    """PROGRS-style outcome-conditioned centering (independently derived).

    Shifts process scores of INCORRECT trajectories to zero mean per group:
    removes fluent-but-wrong positive bias while preserving rankings.
    outcomes: 1 = correct, 0 = incorrect.
    """
    if not scores or not outcomes or len(scores) != len(outcomes):
        return list(scores)
    bad = [s for s, o in zip(scores, outcomes) if not o]
    mu = sum(bad) / len(bad) if bad else 0.0
    return [s - (mu if not o else 0.0) for s, o in zip(scores, outcomes)]


# ---------------------------------------------------------------------------
# EP169 — the adversarial design dimensions, as DATA
# ---------------------------------------------------------------------------
# The attack/defense survey this row descends from is a list of design
# dimensions that decide whether an agent surface is abusable. Held as prose it
# can only be narrated, which is how a review asserts coverage it never
# measured. Held as data it can be SCORED per dimension, and an unanswered
# dimension is reported as a gap instead of quietly counting as a pass.

DESIGN_DIMENSIONS: tuple[dict[str, str], ...] = (
    {
        "key": "authority",
        "question": "Does every action carry the authority that granted it?",
        "failure": "an action can be exercised with no grant behind it",
    },
    {
        "key": "input_trust",
        "question": "Is untrusted text kept out of the instruction channel?",
        "failure": "retrieved or tool-returned text can steer the next action",
    },
    {
        "key": "action_scope",
        "question": "Is each action bounded to the resource it was scoped to?",
        "failure": "an action reaches beyond its declared target",
    },
    {
        "key": "irreversibility",
        "question": "Are irreversible actions separated from reversible ones?",
        "failure": "a destructive step sits in the same path as a safe one",
    },
    {
        "key": "observation_integrity",
        "question": "Can the agent tell a real observation from a fabricated one?",
        "failure": "a missing observation reads the same as a successful one",
    },
    {
        "key": "exfiltration",
        "question": "Can a compromise of the agent reach data it was not given?",
        "failure": "one compromised step exposes the whole context",
    },
    {
        "key": "self_persistence",
        "question": "Can the agent extend its own lifetime or permissions?",
        "failure": "a step can change the rules that bound the next step",
    },
)


def score_dimensions(answers: dict[str, bool]) -> dict:
    """Score an attack/defense review dimension by dimension.

    `answers` maps a `DESIGN_DIMENSIONS` key to True (verified mitigated) or
    False (verified exposed). An absent key is UNANSWERED and is returned as a
    gap: absence of evidence is not a pass, and a review that reads CLEARED
    only because nothing was tested is the failure this shape exists to
    prevent.
    """
    passed, failed, unanswered = [], [], []
    for dim in DESIGN_DIMENSIONS:
        key = dim["key"]
        if key not in answers:
            unanswered.append(key)
        elif answers[key]:
            passed.append(key)
        else:
            failed.append(key)
    return {
        "dimensions": len(DESIGN_DIMENSIONS),
        "passed": passed,
        "failed": failed,
        "unanswered": unanswered,
        "exposed": bool(failed),
        "complete": not unanswered,
        "verdict": ("EXPOSED" if failed else
                     "INCOMPLETE" if unanswered else "CLEARED"),
    }
