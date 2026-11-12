# coding: utf-8
"""Tests for the fitted affect -> construct bridge.

What is being tested, precisely: that the ridge estimator recovers a matrix it was
handed (arithmetic), that the shipped file is the file the tool fits (drift), and
that a missing or malformed file falls back to the hand map instead of raising
(the bridge is an optimisation, not a load-bearing import).

NOT tested, because it cannot be: whether the bridge shape is right for people.
The ground truth is synthetic. A green run here is not evidence of validity and
must not be cited as such.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import fit_affect_bridge as F  # noqa: E402
from PSYCHE import network as N  # noqa: E402


@pytest.fixture(scope="module")
def fitted():
    return F.fit_and_verify(samples=2000, noise=0.02, seed=12345)


def test_estimator_recovers_a_matrix_it_was_handed(fitted):
    """Recovery is judged against the fit's OWN standard error, not a tolerance.

    A fixed tolerance is the mistake this test exists to prevent: at sigma 0.02
    and n=2000 the sampling error on a coefficient is ~4e-4, so any hand-picked
    threshold either fails a correct estimator or passes a broken one.
    """
    assert fitted["recovered"], (
        "worst z %.2f exceeds the %.1f-sigma limit" %
        (fitted["worst_recovery_z"], fitted["recovery_z_limit"]))
    assert fitted["worst_recovery_z"] <= F.RECOVERY_Z


def test_a_deliberately_wrong_estimator_fails_the_same_check():
    """The negative control, in the same change, as RULE 0B-C requires.

    An oracle that cannot fail is not an oracle. Here the "estimate" is the
    intercept-only mean, which cannot recover a slope; the check must reject it.
    """
    design, targets, _, _ = F.synthesise(500, 0.02, 999)
    mean_only = [[sum(row[t] for row in targets) / len(targets)] + [0.0] *
                 (len(F.FEATURES) - 1) for t in range(len(F.TARGETS))]
    ses = F.standard_errors(design, F.RIDGE_LAMBDA, 0.02)
    worst_z = 0.0
    for t_index, name in enumerate(F.TARGETS):
        for i in range(len(F.FEATURES)):
            err = abs(mean_only[t_index][i] - F.W_TRUE[name][i])
            if ses[i]:
                worst_z = max(worst_z, err / ses[i])
    assert worst_z > F.RECOVERY_Z, "the recovery check accepts a zero-slope estimator"


def test_shipped_file_is_the_file_the_tool_fits():
    """Drift, not closeness to truth: a hand-edit is what this catches.

    Comparing the shipped file to W_TRUE would let a plausible hand-edit pass.
    Comparing it to a fresh fit WITH THE TOOL'S OWN DEFAULT SEED is deterministic,
    so any drift at all means a human edited the numbers. The `fitted` fixture
    uses a different seed on purpose (it tests recovery, not drift), so this test
    re-fits at the defaults rather than reusing it.
    """
    fitted = F.fit_and_verify()
    assert F.BRIDGE_FILE.exists(), (
        "src/PSYCHE/data/affect_bridge.json is missing -- run "
        "python tools/fit_affect_bridge.py")
    shipped = json.loads(F.BRIDGE_FILE.read_text(encoding="utf-8"))
    assert shipped["_meta"]["synthetic_ground_truth"] is True
    assert "not" in shipped["_meta"]["provenance"].lower()
    worst = 0.0
    for t_index, name in enumerate(F.TARGETS):
        row = shipped["coefficients"][name]
        worst = max(worst, max(abs(float(row[i]) - fitted["coefficients"][t_index][i])
                               for i in range(len(F.FEATURES))))
    assert worst <= 1e-9, (
        "shipped bridge has drifted from a fresh fit by %.2e: it was edited by hand, "
        "or W_TRUE changed without re-fitting" % worst)


def test_the_two_bridges_are_actually_different(fitted):
    """A comparison between two identical maps would measure nothing.

    This is the guard on the regression the instrument itself had: `hand_map`
    once resolved to the fitted map, so divergence reported 0.001 instead of
    0.135 and every comparison downstream looked reassuringly small.
    """
    div = F.compare(fitted["coefficients"])
    assert div["mean_abs_delta"] > 0.02, (
        "hand and fitted bridges are nearly identical -- either the fit is broken "
        "or the comparison resolved both sides to the same map")
    assert div["top_construct_flip_rate"] > 0.05
    hand = F.hand_map(-0.5, 0.6, -0.4, "sadness")
    fit = F.fitted_map(fitted["coefficients"], -0.5, 0.6, -0.4, "sadness")
    assert any(abs(hand[k] - fit[k]) > 0.01 for k in hand)


def test_missing_bridge_file_falls_back_to_the_hand_map(monkeypatch):
    """The bridge is an optimisation, not a load-bearing import."""
    monkeypatch.setattr(N, "_fitted_bridge", lambda: None)
    assert N.bridge_name() == "hand-map"
    out = N.activation_from_pad((-0.5, 0.6, -0.4), ("sadness",))
    hand = N.hand_activation_from_pad((-0.5, 0.6, -0.4), ("sadness",))
    assert out == hand


def test_malformed_bridge_file_is_a_fallback_not_a_crash(monkeypatch, tmp_path):
    bad = tmp_path / "affect_bridge.json"
    bad.write_text(json.dumps({"coefficients": {"approach": [1.0, 2.0]}}),
                   encoding="utf-8")
    monkeypatch.setattr(N, "_BRIDGE_TRIED", False)
    monkeypatch.setattr(N, "_BRIDGE_CACHE", None)
    monkeypatch.setattr("pathlib.Path.with_name", lambda self, name: tmp_path
                        if name == "data" else Path.with_name(self, name))
    assert N.bridge_name() == "hand-map"
    assert N.activation_from_pad((0.0, 0.0, 0.0)) == \
        N.hand_activation_from_pad((0.0, 0.0, 0.0))


def test_the_loop_stamps_which_bridge_produced_the_graph():
    from PSYCHE.intuition import IntuitionIndex
    from PSYCHE.rt_agent import TherapeuticLoop

    net = N.PersonNetwork()
    loop = TherapeuticLoop(net, IntuitionIndex(net))
    loop.turn("I have been sleeping badly this week", observe=False)
    assert net.activation_source == N.bridge_name()
    assert net.activation_source in ("fitted-ridge", "hand-map")
    assert net.to_dict()["activation_source"] == net.activation_source
