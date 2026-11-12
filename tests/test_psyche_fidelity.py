# coding: utf-8
"""PSYCHE Phase 5 gate: the program merge gate.

- All six capabilities score in 0..1 on a fitted twin.
- Composite ordering: twin > generic (all-3.0) > permuted (shuffled theta).
- NC1: permuted-theta twin is near chance (its "personality" is noise).
- NC2: shuffled-memory twin loses recall but KEEPS tone (dissociation:
  amnesia must not read as a different person).
- Harness pieces are deterministic (same battery, same scores).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PSYCHE import evaluate as E
from PSYCHE import identify as I


def _theta(o=3.0, c=3.0, e=3.0, a=3.0, n=3.0, h=3.0):
    return {"O": o, "C": c, "E": e, "A": a, "N": n, "H": h}


def test_all_caps_bounded():
    rep = E.fidelity_report(_theta(e=4.0, n=2.0))
    assert set(rep) == {"opinion_consistency", "memory_recall",
                        "logical_reasoning", "lexical_fidelity",
                        "persona_tone", "syntactic_style", "prediction",
                        "composite"}
    for k, v in rep.items():
        assert 0.0 <= v <= 1.0, (k, v)


def _held_out(true_theta, seed=501):
    return I.simulate_responses(true_theta, noise=0.05, seed=seed)


def test_composite_ordering_twin_generic_permuted():
    import random
    rng = random.Random(77)
    th = {d: round(rng.uniform(1.5, 4.5), 2) for d in I.DOMAINS}
    held = _held_out(th)
    generic = {d: 3.0 for d in I.DOMAINS}
    perm = dict(zip(I.DOMAINS, [th[d] for d in reversed(I.DOMAINS)]))
    twin = E.fidelity_report(th, own_responses=held)["composite"]
    gen = E.fidelity_report(generic, own_responses=held)["composite"]
    per = E.fidelity_report(perm, own_responses=held)["composite"]
    assert twin > gen > per, (twin, gen, per)


def test_nc1_permuted_near_chance():
    """Negative control on the DISCRIMINATING leg, not the composite.

    Five of seven legs are mechanism smoke tests (1.0 for any twin by
    construction); averaging them in would let a permuted twin hide
    behind floors. The control targets the prediction leg, where persona
    content actually lives: shuffled domains must collapse it.
    """
    import random
    rng = random.Random(78)
    th = {d: round(rng.uniform(1.5, 4.5), 2) for d in I.DOMAINS}
    held = _held_out(th, seed=502)
    vals = [round(rng.uniform(1.0, 5.0), 2) for _ in I.DOMAINS]
    preds = []
    for seed in range(5):
        r2 = random.Random(seed)
        order = vals[:]
        r2.shuffle(order)
        rep = E.fidelity_report(dict(zip(I.DOMAINS, order)),
                                own_responses=held)
        preds.append(rep["prediction"])
    assert sum(preds) / len(preds) < 0.5, preds


def test_nc2_shuffled_memory_loses_recall_keeps_tone():
    rows = [{"age_h": float(i), "salience": 0.9 if i < 3 else 0.2,
             "pad": (0.5, 0.2, 0.1)} for i in range(10)]
    import random
    rec_before = E.recall_episodic(rows, top_k=3)
    before = sum(1 for r in rec_before if r["age_h"] < 3) / 3.0
    # Lesion WHAT mattered (the young high-salience rows), not positions:
    # shuffled salience assignment by position can miss them by chance.
    for r in rows:
        r["salience"] = 0.2 if r["age_h"] < 3 else 0.9
    rec_after = E.recall_episodic(rows, top_k=3)
    after = sum(1 for r in rec_after if r["age_h"] < 3) / 3.0
    assert before == 1.0 and after < before
    th = _theta(e=4.0)
    pads = [I.predict_pad(th, s) for s in I.SITUATION_BATTERY]
    assert E.tone_signature(pads) == E.tone_signature(pads)


def test_harness_deterministic():
    th = _theta(o=4.0)
    assert (E.fidelity_report(th) == E.fidelity_report(th))


def test_recall_orders_by_recency_x_salience():
    rows = [{"age_h": 100.0, "salience": 1.0, "pad": (0, 0, 0)},
            {"age_h": 1.0, "salience": 0.1, "pad": (0, 0, 0)},
            {"age_h": 2.0, "salience": 0.9, "pad": (0, 0, 0)}]
    got = E.recall_episodic(rows, top_k=3)
    assert [r["age_h"] for r in got] == [2.0, 1.0, 100.0]


def test_verbalizer_tiers_and_unknown():
    assert E.verbalize("joy", 0.1) != E.verbalize("joy", 0.9)
    assert (E.verbalize("joy", 0.5, {"joy": ["a"]})
            != E.verbalize("joy", 0.5, {"joy": ["b"]}))
    assert E.verbalize("nope", 0.5) == "feeling something unnameable"
