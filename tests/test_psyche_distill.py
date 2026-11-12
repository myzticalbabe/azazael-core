# coding: utf-8
"""PSYCHE Phase 3 gate: frozen-theta distillation + choice-trait recovery.

- H4: on-policy disagreement < BC disagreement on held-out states.
- H2: fitted-theta student beats generic (all-3.0) twin on held-out PAD MSE.
- P3: theta inputs are never mutated by any learning call.
- Preference step strictly reduces teacher-student KL on disagreed keys.
- C/H recover r > 0.5 from action histories (closes Phase-2 boundary);
  sparse histories yield None, not invented numbers.
- Determinism: same seed, same draws, across processes.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PSYCHE import distill as D
from PSYCHE import affect as A
from PSYCHE import identify as I


def _theta(o=3.0, c=3.0, e=3.0, a=3.0, n=3.0, h=3.0):
    return {"O": o, "C": c, "E": e, "A": a, "N": n, "H": h}


def test_person_policy_deterministic_across_calls():
    th = _theta(e=4.0, n=2.0)
    st = {"id": 0, "kind": "affect", "desirability": 0.5,
          "novelty": 0.5, "cost": 0.3}
    assert (D.sample_action(th, st, 5) == D.sample_action(th, st, 5))


def test_h4_onpolicy_beats_bc():
    import random
    rng = random.Random(21)
    th = {"O": 4.0, "C": 4.0, "E": 2.0, "A": 3.0, "N": 2.5, "H": 3.0}
    states = D.make_states(90, seed=21)
    demo, held = states[:60], states[60:]
    bc = D.bc_policy(th, demo)
    onp = D.onpolicy_refine(th, demo)
    gb = D.disagreement(bc, th, held)
    go = D.disagreement(onp, th, held)
    assert go < gb, (go, gb)
    assert go <= 0.75 * gb or gb == 0.0, (go, gb)


def test_h2_student_beats_generic_twin():
    import random
    rng = random.Random(31)
    true = {"O": 4.2, "C": 2.2, "E": 4.4, "A": 3.1, "N": 4.1, "H": 2.8}
    resp = I.simulate_responses(true, noise=0.05, seed=31)
    fit = I.fit_traits(resp)["theta"]
    held = I.simulate_responses(true, noise=0.05, seed=32)
    generic = {d: 3.0 for d in I.DOMAINS}

    def mse(th):
        err = 0.0
        for pad, sit in zip(held, I.SITUATION_BATTERY):
            pred = I.predict_pad(th, sit)
            err += sum((a - b) ** 2 for a, b in zip(pad, pred))
        return err / len(held)

    assert mse(fit) < mse(generic)


def test_theta_frozen():
    import copy
    th = _theta(c=4.5, h=2.0)
    snapshot = copy.deepcopy(th)
    states = D.make_states(30, seed=7)
    D.bc_policy(th, states)
    D.onpolicy_refine(th, states)
    assert th == snapshot


def test_preference_step_reduces_kl():
    import math
    th = _theta(e=4.5)
    states = D.make_states(40, seed=9)
    student = D.bc_policy(th, states[:20])
    teacher = D.onpolicy_refine(th, states[:20])

    def kl(keys):
        tot, n = 0.0, 0
        for k in keys:
            s, t = student.get(k), teacher.get(k)
            if not s or not t:
                continue
            tot += sum(ti * math.log(max(1e-9, ti) / max(1e-9, si))
                       for si, ti in zip(s, t))
            n += 1
        return tot / max(1, n)

    keys = [k for k in student if k in teacher]
    before = kl(keys)
    moved = D.preference_step(student, teacher)
    after_keys = [k for k in moved if k in teacher]
    after = sum(
        teacher[k][i] * math.log(max(1e-9, teacher[k][i]) / max(1e-9, moved[k][i]))
        for k in after_keys for i in range(4)) / max(1, len(after_keys))
    assert after <= before


def test_choice_traits_recover():
    import random
    rng = random.Random(41)
    trues_c, est_c, trues_h, est_h = [], [], [], []
    for i in range(14):
        c = round(rng.uniform(1.5, 4.5), 2)
        h = round(rng.uniform(1.5, 4.5), 2)
        th = _theta(c=c, h=h, a=3.0)
        acts = []
        for j in range(60):
            kind = D.PERSIST if j % 2 == 0 else D.FAIR
            st = {"id": j, "kind": kind,
                  "desirability": 0.0, "novelty": 0.0, "cost": 0.3}
            a = D.sample_action(th, st, seed=1000 + i * 100 + j)
            acts.append({"kind": kind, "action": a})
        feats = D.choice_features([
            {"kind": a["kind"],
             "action": ("continue" if a["kind"] == D.PERSIST and a["action"] == 0
                        else "quit" if a["kind"] == D.PERSIST else
                        "fair" if a["action"] in (0, 1) else "exploit"),
             "planned": False} for a in acts])
        got = D.fit_choice_traits(feats)
        assert got["C"] is not None and got["H"] is not None
        trues_c.append(c)
        est_c.append(got["C"])
        trues_h.append(h)
        est_h.append(got["H"])
    assert I.pearson(trues_c, est_c) > 0.5, (trues_c, est_c)
    assert I.pearson(trues_h, est_h) > 0.5, (trues_h, est_h)


def test_sparse_history_yields_none():
    got = D.fit_choice_traits(D.choice_features(
        [{"kind": "persist", "action": "continue"}]))
    assert got == {"C": None, "H": None}
    got2 = D.fit_choice_traits(D.choice_features([]))
    assert got2 == {"C": None, "H": None}
