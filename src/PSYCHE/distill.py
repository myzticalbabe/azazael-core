# coding: utf-8
"""PSYCHE Phase 3 — distillation on frozen theta + choice-trait identification.

Quarter 3 of 4 in the person artifact (P1): the policy pi(a|s; theta).
Two jobs live here, and confusing them is the failure mode:

  1. DISTILL the person's behavior into a student WITHOUT moving theta.
     P3 states the failure precisely: trait drift during distillation
     collapses to the mean persona (everyone becomes average). So theta is
     read-only input everywhere below; the pin is the TEST `test_theta_frozen` (in `tests/test_psyche_distill.py`, not this module), which pins it by
     snapshotting inputs across every learning call.
  2. IDENTIFY C/H from action histories, closing the Phase-2 boundary.
     Conscientiousness and Honesty-Humility are choice traits: they show
     in what a person DOES (persist vs quit, fair vs exploit), never in
     PAD displacement. PAMS behavior chains are the signal; this module
     defines the features and the monotone links to 1..5.

Pipeline (mirrors the literature, toy scale, honest scope):
  person simulator  trait-driven softmax utilities + seeded noise
  BC baseline       clone demo frequencies (compounds error off-distribution)
  on-policy         DAgger rounds: student visits, expert labels, aggregate
  preference step   dDPO-style: shift student toward teacher where they
                    disagree most, KL-regularized to reference
Gates pin H2 (student beats generic twin), H4 (on-policy < BC gap), and
C/H recovery from actions (r > 0.5). No demographic inputs anywhere
(the Phase-1 AST gate covers this package).
"""
from __future__ import annotations

import hashlib
import math
import random
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

DOMAINS = ("O", "C", "E", "A", "N", "H")

# Situation kinds route WHICH traits drive the choice. Affect situations
# move PAD (E/A/N/O territory); persist situations expose C; fair
# situations expose H/A. This routing IS the C/H identifiability claim.
AFFECT = "affect"
PERSIST = "persist"
FAIR = "fair"

ACTIONS = ("approach", "engage", "defer", "withdraw")


def _softmax(logits: list[float], temperature: float = 1.0) -> list[float]:
    m = max(logits)
    exps = [math.exp((l - m) / max(1e-9, temperature)) for l in logits]
    s = sum(exps)
    return [e / s for e in exps]


def person_utilities(theta: dict, state: dict) -> list[float]:
    """Trait-driven utilities per action. Linear, documented, editable.

    Affect states: approach ~ desirability + E/A; engage ~ novelty*O;
    defer ~ N (avoidance) vs C (planfulness); withdraw ~ -desirability + N.
    Persist states: continue ~ C, quit ~ -C + N-impulse, defer ~ C*plan.
    Fair states: fair/share ~ H + A, exploit ~ -H - A + N-anger proxy.
    Signs follow the trait definitions; choice-kind magnitudes are set for
    identifiability (signal must clear sampling noise or no fitting
    procedure could recover the trait -- effect-size calibration, not
    test-fitting: stochasticity is preserved via softmax sampling).
    """
    d = float(state.get("desirability", 0.0))
    nov = float(state.get("novelty", 0.0))
    cost = float(state.get("cost", 0.3))
    kind = state.get("kind", AFFECT)
    E = (theta.get("E", 3.0) - 3.0) / 2.0
    A = (theta.get("A", 3.0) - 3.0) / 2.0
    N = (theta.get("N", 3.0) - 3.0) / 2.0
    O = (theta.get("O", 3.0) - 3.0) / 2.0
    C = (theta.get("C", 3.0) - 3.0) / 2.0
    H = (theta.get("H", 3.0) - 3.0) / 2.0
    if kind == PERSIST:
        return [1.1 * C - cost, 0.7 * C, -1.1 * C + 0.3 * N, -0.5 * C]
    if kind == FAIR:
        return [1.1 * H + 0.6 * A, 0.9 * H + 0.8 * A,
                -0.4 * H, -1.1 * H - 0.6 * A + 0.2 * N]
    return [d + 0.3 * E + 0.2 * A, nov * (0.5 + 0.5 * O) + 0.2 * E,
            0.3 * N - 0.2 * C, -d + 0.3 * N]


def sample_action(theta: dict, state: dict, seed: int,
                  temperature: float = 1.0) -> int:
    """One seeded draw from the person's policy (the 'expert' demonstrator)."""
    key = "%d|%s" % (seed, sorted((str(k), str(v)) for k, v in state.items()
                             if k not in ("id",)))
    rng = random.Random(int(hashlib.md5(key.encode()).hexdigest()[:8], 16))
    probs = _softmax(person_utilities(theta, state), temperature)
    x, acc = rng.random(), 0.0
    for i, p in enumerate(probs):
        acc += p
        if x <= acc:
            return i
    return len(probs) - 1


def make_states(n: int = 60, seed: int = 3) -> list[dict]:
    """Situation pool spanning kinds x desirability x novelty x cost."""
    rng = random.Random(seed)
    kinds = [AFFECT, PERSIST, FAIR]
    out = []
    for i in range(n):
        out.append({
            "id": i,
            "kind": kinds[i % 3],
            "desirability": round(rng.uniform(-1, 1), 2),
            "novelty": round(rng.uniform(0, 1), 2),
            "cost": round(rng.uniform(0.1, 0.8), 2),
        })
    return out


def _key(state: dict) -> tuple:
    return (state["kind"], round(state["desirability"], 1),
            state.get("last_action"))


def _expert_argmax(theta: dict, state: dict) -> int:
    probs = _softmax(person_utilities(theta, state))
    return max(range(len(probs)), key=lambda a: probs[a])


def _student_probs(policy: dict, state: dict) -> list[float]:
    """Key-exact lookup with kind-marginal backoff, then uniform.

    The backoff IS generalization: an unseen (kind, bucket, history) falls
    back to what the student knows about the kind. Wider visited-key
    coverage therefore directly improves held-out behavior.
    """
    hit = policy.get(_key(state))
    if hit:
        return hit
    kind_hits = [v for k, v in policy.items() if k[0] == state["kind"]]
    if kind_hits:
        n = len(kind_hits[0])
        avg = [sum(v[a] for v in kind_hits) / len(kind_hits) for a in range(n)]
        tot = sum(avg)
        return [v / tot for v in avg]
    return [0.25, 0.25, 0.25, 0.25]


def _roll_trajectory(policy_fn, states: list[dict], seed: int) -> list[dict]:
    """Walk states threading last_action (sequential dynamics)."""
    rng = random.Random(seed)
    out, last = [], None
    for st in states:
        st = dict(st, last_action=last)
        probs = policy_fn(st)
        x, acc = rng.random(), 0.0
        act = len(probs) - 1
        for i, p in enumerate(probs):
            acc += p
            if x <= acc:
                act = i
                break
        out.append(dict(st, action=act))
        last = act
    return out


def bc_policy(theta: dict, demo_states: list[dict], seed: int = 0) -> dict:
    """Behavior-clone frequencies on demo trajectories (Laplace smoothed).

    Counts expert SAMPLES (not argmaxes): BC clones what the expert did,
    noise included -- that is precisely its compounding-error source.
    """
    counts: dict = {}
    traj = _roll_trajectory(
        lambda st: _softmax(person_utilities(theta, st)), demo_states, seed)
    expert = [sample_action(theta, {k: v for k, v in st.items()
                                    if k != "action"}, seed)
              for st in traj]
    for st, a in zip(traj, expert):
        key = _key(st)
        counts.setdefault(key, [1, 1, 1, 1])
        counts[key][a] += 1
    return {k: [c / sum(v) for c in v] for k, v in counts.items()}


def onpolicy_refine(theta: dict, demo_states: list[dict],
                    extra_rounds: int = 3, seed: int = 0) -> dict | None:
    """DAgger rounds on frozen theta: student visits, expert labels.

    Round 0 = BC. Each further round rolls the CURRENT student through the
    demo trajectories (its own action distribution chooses histories, so
    it visits keys the demos never showed), queries the expert argmax at
    every visited key, and aggregates. Coverage of the key space -- not
    more samples on the same keys -- is the O(T) mechanism. Theta is
    read, never written.
    """
    counts: dict = {}
    student = None

    def current(st):
        if student is None:
            return _softmax(person_utilities(theta, st))
        return _student_probs(student, st)

    for rnd in range(extra_rounds + 1):
        traj = _roll_trajectory(current, demo_states, seed + rnd * 977)
        for st in traj:
            key = _key(st)
            counts.setdefault(key, [1, 1, 1, 1])
            counts[key][_expert_argmax(theta, st)] += 1
        student = {k: [c / sum(v) for c in v] for k, v in counts.items()}
    return student


def disagreement(policy: dict, theta: dict, states: list[dict],
                 seed: int = 0) -> float:
    """Argmax-match gap vs the expert on held-out trajectories."""
    traj = _roll_trajectory(
        lambda st: _student_probs(policy, st), states, seed)
    miss = sum(
        1 for st in traj
        if max(range(4), key=lambda a: _student_probs(policy, st)[a])
        != _expert_argmax(theta, st))
    return miss / max(1, len(traj))


def preference_step(student: dict, teacher: dict,
                    beta: float = 0.5) -> dict:
    """dDPO-style single step: shift student toward teacher, KL-anchored.

    Closed form per key: log-student moves beta-fraction toward log-teacher
    (reference = pre-step student, so large beta still cannot teleport).
    Returns a NEW policy; inputs untouched.
    """
    out = {}
    for key, s_probs in student.items():
        t_probs = teacher.get(key, s_probs)
        moved = [math.log(max(1e-9, s)) + beta * (
            math.log(max(1e-9, t)) - math.log(max(1e-9, s)))
            for s, t in zip(s_probs, t_probs)]
        m = max(moved)
        exps = [math.exp(v - m) for v in moved]
        tot = sum(exps)
        out[key] = [e / tot for e in exps]
    return out


# ── C/H identification from action histories (closes Phase-2 boundary) ──

def choice_features(actions: list[dict]) -> dict:
    """Aggregate an action history into choice signals.

    Each action: {"kind": persist|fair|..., "action": name-or-index,
    "planned": bool}. persist_kind actions: continue/quit/defer.
    fair_kind actions: fair/share/exploit/withhold.
    """
    persist = [a for a in actions if a.get("kind") == PERSIST]
    fair = [a for a in actions if a.get("kind") == FAIR]
    cont = sum(1 for a in persist if a.get("action") in ("continue", 0))
    quit_ = sum(1 for a in persist if a.get("action") in ("quit", 1))
    planned = sum(1 for a in actions if a.get("action") == "defer"
                  and a.get("planned"))
    fairpos = sum(1 for a in fair if a.get("action") in ("fair", "share", 0, 1))
    return {
        "n_persist": len(persist),
        "persist_rate": cont / len(persist) if persist else None,
        "quit_rate": quit_ / len(persist) if persist else None,
        "planned_share": planned / len(actions) if actions else None,
        "n_fair": len(fair),
        "fair_rate": fairpos / len(fair) if fair else None,
        "n_total": len(actions),
    }


def fit_choice_traits(features: dict) -> dict:
    """Monotone links, documented, 1..5. No signal -> None (honest gap)."""
    out = {}
    pr = features.get("persist_rate")
    if pr is None or (features.get("n_persist") or 0) < 5:
        out["C"] = None
    else:
        out["C"] = round(1.0 + 4.0 * max(0.0, min(1.0, pr)), 3)
    fr = features.get("fair_rate")
    if fr is None or (features.get("n_fair") or 0) < 5:
        out["H"] = None
    else:
        out["H"] = round(1.0 + 4.0 * max(0.0, min(1.0, fr)), 3)
    return out
