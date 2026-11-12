"""Tests for eve_unleashed.well -- golden replica, native fix, aether gate."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from eve_unleashed.well import (
    aether_verify,
    fix_neural_space,
    golden_digest,
    golden_tensor,
    replicate_golden,
)


def test_golden_deterministic_across_calls():
    assert golden_tensor(7, 8, 8) == golden_tensor(7, 8, 8)


def test_golden_digest_stable_and_seed_sensitive():
    assert golden_digest(golden_tensor(7, 4, 4)) == golden_digest(golden_tensor(7, 4, 4))
    assert golden_digest(golden_tensor(7, 4, 4)) != golden_digest(golden_tensor(8, 4, 4))


def test_replicate_anchors_then_matches():
    anchor = replicate_golden(7, 8, 8)
    assert anchor["anchored"] and anchor["match"]
    again = replicate_golden(7, 8, 8, expected=anchor["digest"])
    assert again["match"] and not again["anchored"]


def test_replicate_catches_tamper():
    anchor = replicate_golden(7, 8, 8)
    bad = anchor["digest"][:-1] + ("0" if anchor["digest"][-1] != "0" else "1")
    assert replicate_golden(7, 8, 8, expected=bad)["match"] is False


def test_fix_q80_within_native_bound():
    proof = fix_neural_space(golden_tensor(7, 8, 8), "Q8_0")
    assert proof["within_bound"] and proof["max_err"] <= 0.01


def test_fix_q40_looser_than_q80_but_bounded():
    q8 = fix_neural_space(golden_tensor(7, 8, 8), "Q8_0")
    q4 = fix_neural_space(golden_tensor(7, 8, 8), "Q4_0")
    assert q4["within_bound"] and q4["max_err"] >= q8["max_err"]


def test_aether_gate_clean_on_honest_proof_and_refuses_sabotage():
    good = aether_verify(fix_neural_space(golden_tensor(7, 8, 8), "Q8_0"))
    assert good["clean"] and good["verdict"] == "PASS"
    sabotaged = aether_verify({"within_bound": True, "n": 0})
    assert sabotaged["clean"] is False
