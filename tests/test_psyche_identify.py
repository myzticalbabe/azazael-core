# coding: utf-8
"""PSYCHE Phase 2 gate: identifiability under the model (H3's cheapest test).

- Battery spans all OCC branches (event/action/object, prospect/confirmed,
  self/other, neutral fillers).
- Forward model is deterministic at zero noise.
- Synthetic persons (spread thetas, seeded noise) recover at r > 0.5 per
  domain: the artifact's core claim (behavior in, traits out) holds where
  it must hold first. Human validity is Phase 5, not here.
- Neutral responses stay neutral (the prior is honest).
- Fit beats the do-nothing prior on MSE.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PSYCHE import identify as I
from PSYCHE import affect as A


def _spread_thetas(n: int):
    import random
    rng = random.Random(11)
    return [{d: round(rng.uniform(1.5, 4.5), 2) for d in I.DOMAINS}
            for _ in range(n)]


def test_battery_covers_all_branches():
    kinds = {s["kind"] for s in I.SITUATION_BATTERY}
    assert kinds == {"event", "action", "object"}
    assert len(I.SITUATION_BATTERY) >= 12
    pulls = [I.situation_pull(s) for s in I.SITUATION_BATTERY]
    assert any(p != (0.0, 0.0, 0.0) for p in pulls)
    assert any(p == (0.0, 0.0, 0.0) for p in pulls)  # neutral filler present


def test_forward_deterministic_at_zero_noise():
    theta = {"O": 4.0, "C": 3.0, "E": 2.0, "A": 4.0, "N": 2.0, "H": 3.0}
    a = I.simulate_responses(theta, noise=0.0, seed=1)
    b = I.simulate_responses(theta, noise=0.0, seed=99)
    assert a == b


def test_recovery_correlation_above_half():
    trues = _spread_thetas(12)
    fitted = []
    for i, th in enumerate(trues):
        resp = I.simulate_responses(th, noise=0.05, seed=100 + i)
        fitted.append(I.fit_traits(resp))
    rep = I.recovery_report(trues, fitted)
    for d in ("O", "E", "A", "N"):
        assert rep[d] > 0.5, (d, rep[d])


def test_conscientiousness_honesty_are_affect_blind():
    """Documented boundary, not a failure: C and H are choice traits
    (planfulness, fairness under temptation), not affective traits. PAD
    displacement carries almost no signal for them (H has no temperament
    weights at all, by design -- nothing about honesty moves baseline
    affect). They identify from ACTION histories (PAMS behavior chains,
    Phase 3/5), never from feelings. Pinning the blindness keeps anyone
    from "fixing" it by inventing affective channels per trait."""
    trues = _spread_thetas(12)
    fitted = []
    for i, th in enumerate(trues):
        resp = I.simulate_responses(th, noise=0.05, seed=200 + i)
        fitted.append(I.fit_traits(resp))
    rep = I.recovery_report(trues, fitted)
    assert rep["H"] < 0.5, rep
    assert rep["C"] < 0.7, rep


def test_neutral_stays_neutral():
    base = A.temperament({d: 3.0 for d in I.DOMAINS})
    resp = [base for _ in I.SITUATION_BATTERY]
    got = I.fit_traits(resp)["theta"]
    for d in I.DOMAINS:
        assert abs(got[d] - 3.0) < 0.6, (d, got[d])


def test_fit_beats_prior():
    th = {"O": 4.5, "C": 2.0, "E": 4.0, "A": 2.5, "N": 4.0, "H": 3.5}
    resp = I.simulate_responses(th, noise=0.05, seed=5)
    got = I.fit_traits(resp)
    assert got["mse"] < got["prior_mse"]


def test_outputs_json_serializable():
    import json
    th = {"O": 4.0, "C": 3.0, "E": 2.0, "A": 4.0, "N": 2.0, "H": 3.0}
    resp = I.simulate_responses(th, noise=0.0, seed=2)
    got = I.fit_traits(resp)
    json.dumps({"theta": got["theta"], "report": I.recovery_report([th], [got])})
