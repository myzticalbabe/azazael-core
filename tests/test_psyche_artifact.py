# coding: utf-8
"""PSYCHE Phase 4 gate: artifact integrity + H5 archetype decodability.

- Roster shape: 12 archetypes, gift/shadow text, directions, centroids.
- H5: persons sampled near each centroid classify back above chance
  (chance 1/12; bar 0.5 with margin).
- Polarity flips gift->shadow under high-N low-D distress, and stays
  gift for the calm profile.
- Artifact round-trips losslessly; edits persist (editable property).
- Invalid artifacts fail loudly with listed problems (never half-load).
- Decode is deterministic.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest

from PSYCHE import artifact as P


def _near_centroid(name, spread=0.15, seed=0):
    import random
    rng = random.Random(seed)
    _gift, _shadow, direction, centroid = P.ARCHETYPES[name]
    theta = {d: round(3.0 + 2.0 * direction.get(d, 0.0)
                      + rng.uniform(-spread, spread), 3)
             for d in P.DOMAINS}
    theta = {d: max(1.0, min(5.0, v)) for d, v in theta.items()}
    pad = tuple(max(-1.0, min(1.0, c + rng.uniform(-spread, spread)))
                for c in centroid)
    return theta, pad


def test_roster_shape():
    assert len(P.ARCHETYPES) == 12
    for name, (gift, shadow, direction, centroid) in P.ARCHETYPES.items():
        assert name.startswith("The "), name
        assert gift and shadow
        assert len(centroid) == 3
        assert set(direction) <= set(P.DOMAINS)


def test_h5_decode_above_chance():
    hits, n = 0, 0
    for i, name in enumerate(P.ARCHETYPES):
        theta, pad = _near_centroid(name, seed=100 + i)
        got = P.decode_archetype(theta, pad)
        n += 1
        hits += got["archetype"] == name
        assert got["margin"] >= 0.0
    acc = hits / n
    assert acc > 0.5, (hits, n)  # chance is 1/12


def test_polarity_flips_on_reactivity_without_agency():
    theta = {"O": 3.0, "C": 3.0, "E": 3.0, "A": 3.0, "N": 5.0, "H": 3.0}
    shadowed = P.decode_archetype(theta, (-0.5, 0.4, -0.4))
    assert shadowed["expression"] == "shadow"
    calm = {"O": 3.0, "C": 3.0, "E": 3.0, "A": 3.0, "N": 1.5, "H": 3.0}
    gifted = P.decode_archetype(calm, (0.5, 0.2, 0.4))
    assert gifted["expression"] == "gift"


def _artifact():
    return P.PersonArtifact(
        name="probe",
        theta={"O": {"intellect": 4.0}, "C": {"orderliness": 3.0},
               "E": {"gregariousness": 2.0}, "A": {"trust": 4.0},
               "N": {"anxiety": 2.0}, "H": {"fairness": 4.0}},
        memory={"store": "pams", "query": "recent", "span": "30d",
                "n_rows": 120},
        affect={"weights": "default", "regulation": {"reappraisal": 1.0},
                "display_rules": None, "decay_rates": {"event": 0.5}},
        policy={"kind": "distilled-tabular", "ref": "models/probe.json",
                "trained_on": {"n_traj": 60}})


def test_artifact_round_trip_and_editable():
    a = _artifact()
    b = P.PersonArtifact.from_json(a.to_json())
    assert b.to_dict() == a.to_dict()
    b.theta["O"]["intellect"] = 2.0
    assert P.PersonArtifact.from_json(b.to_json()).theta["O"]["intellect"] == 2.0


def test_invalid_artifacts_fail_loudly():
    assert P.validate_artifact({}) != []
    assert P.validate_artifact({"version": "nope"}) != []
    bad = _artifact().to_dict()
    del bad["theta"]["N"]
    probs = P.validate_artifact(bad)
    assert any("N" in p for p in probs)
    with pytest.raises(ValueError):
        P.PersonArtifact.from_dict(bad)
    with pytest.raises(ValueError):
        P.PersonArtifact.from_dict({"version": "psyche.v0"})


def test_decode_deterministic():
    theta = {"O": 4.0, "C": 3.0, "E": 4.0, "A": 3.0, "N": 2.0, "H": 3.0}
    assert (P.decode_archetype(theta, (0.4, 0.3, 0.3))
            == P.decode_archetype(theta, (0.4, 0.3, 0.3)))
