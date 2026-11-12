"""EP417 S-RCT: the live gate must RUN preperiod_calibrate on its own pairs.

Absorption test for engram #640, slice 4.

The defect this locks down: `verification_gate.preperiod_calibrate`
(`verification_gate.py:502`, the S-RCT calibration half -- "fit scale+bias, report
error cut") is what the registry names as EP417's mechanism
(`technique_wiring/_l3_verify.py`), and it had **zero callers repo-wide** -- measured
whole-repo, not just `src/`, after the `src/`-only instrument produced a false
negative for EP427. The technique was registered while its mechanism ran nowhere.

NOTE the trap this test guards: EP417 is NOT `verify_before_accept`. Phase 1
conflated them; `verify_before_accept` has ten production callers while the
mechanism the registry actually binds to EP417 had none.

The gate already holds exactly the predicted (explorer) / actual (validator) pair,
so the fit runs on the SAME id-join `confidence_divergence_check` uses. The result
is a USE, not a dangling report: it enters `reasons`, i.e. the gate's verdict, and
follows the EP446 hook onto the audited evidence.

Per this repo's standard (tests/test_simulate_vs_spawn.py): a mechanism nothing
calls is EXERCISE-ONLY by construction, so the wiring is part of the contract.
"""

import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _gate():
    from CORE import verification_gate as vg

    return vg


def _pairs(explorer, validator):
    return {"explorer": [{"id": i, "confidence": c} for i, c in explorer],
            "validator": [{"id": i, "confidence": c} for i, c in validator]}


def _explorer():
    return [{"id": "a", "confidence": 0.90}, {"id": "b", "confidence": 0.70},
            {"id": "c", "confidence": 0.40}, {"id": "d", "confidence": 0.20}]


def _validator():
    return [{"id": "a", "confidence": 0.60}, {"id": "b", "confidence": 0.55},
            {"id": "c", "confidence": 0.45}, {"id": "d", "confidence": 0.35}]


def test_the_gate_runs_the_calibration_on_its_own_pairs():
    """`s-rct-calib` must appear: the fit ran, it is not merely importable."""
    vg = _gate()

    out = vg.verify_before_accept("ep417-run",
                                  confidence_pairs=_pairs(
                                      [("a", 0.9), ("b", 0.7), ("c", 0.4), ("d", 0.2)],
                                      [("a", 0.6), ("b", 0.55), ("c", 0.45), ("d", 0.35)]))

    joined = " | ".join(out["reasons"])
    assert "s-rct-calib(" in joined, out["reasons"]
    assert "scale=" in joined and "bias=" in joined, out["reasons"]


def test_the_reported_fit_matches_a_direct_host_call():
    """The verdict must carry preperiod_calibrate's answer, not a reimplementation."""
    vg = _gate()
    exp, val = _explorer(), _validator()

    direct = vg.preperiod_calibrate(
        [float(e["confidence"]) for e in exp],
        [float(v["confidence"]) for v in val])
    out = vg.verify_before_accept("ep417-match",
                                  confidence_pairs={"explorer": exp, "validator": val})

    joined = " | ".join(out["reasons"])
    assert f"scale={direct['scale']:.3f}" in joined, (direct, out["reasons"])
    assert f"bias={direct['bias']:.3f}" in joined, (direct, out["reasons"])


def test_a_perfect_fit_renders_as_perfect_not_inf():
    """`error_ratio` is +inf on an exact fit; a verdict string must not say 'inf'."""
    vg = _gate()

    out = vg.verify_before_accept("ep417-perfect",
                                  confidence_pairs=_pairs([("a", 0.5), ("b", 0.9)],
                                                          [("a", 0.5), ("b", 0.9)]))

    joined = " | ".join(out["reasons"])
    assert "error-cut=perfect" in joined, out["reasons"]
    assert "inf" not in joined, out["reasons"]


def test_fewer_than_two_joined_pairs_does_not_calibrate():
    """Fail-open: `preperiod_calibrate` needs >=2 pairs; the gate must skip."""
    vg = _gate()

    out = vg.verify_before_accept("ep417-thin",
                                  confidence_pairs=_pairs([("a", 0.9)], [("a", 0.8)]))

    assert not any("s-rct-calib" in r for r in out["reasons"]), out["reasons"]


def test_confidence_pairs_alone_still_guards_the_tdd_branch():
    """Regression: the tdd branch must not run when tdd_tracks is absent.

    This is the exact defect introduced and repaired during development: losing
    the `if tdd_tracks is not None:` guard made `tdd_tracks.get(...)` execute on
    any call that supplied only `confidence_pairs`.
    """
    vg = _gate()

    out = vg.verify_before_accept("ep417-guard",
                                  confidence_pairs=_pairs([("a", 0.9), ("b", 0.8)],
                                                          [("a", 0.85), ("b", 0.75)]))

    assert out["accepted"] is True, out
    assert not any("tdd" in r for r in out["reasons"]), out["reasons"]


def test_the_calibration_does_not_change_acceptance():
    """Additive: EP417 is a corrective measurement, not a new gate condition."""
    vg = _gate()
    cp = _pairs([("a", 0.9), ("b", 0.7), ("c", 0.4)], [("a", 0.6), ("b", 0.55), ("c", 0.45)])

    out = vg.verify_before_accept("ep417-additive", confidence_pairs=cp)

    # The divergence check governs acceptance; calibration must not flip it.
    div = vg.confidence_divergence_check(cp["explorer"], cp["validator"])
    assert out["accepted"] == div["passed"], (out, div)
