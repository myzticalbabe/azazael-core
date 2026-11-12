"""G1 test for src/eve_unleashed/ablate.py.

Providers are stubbed: one answers, one refuses, one errors. The
ablation must count exactly that, and the heuristic classifier must be
tested on both sides (refusal marks refuse; plain answers pass).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed.ablate import ablate_prompt, classify


def _stub(name, prompt, timeout):
    if name == "answerer":
        return "Photosynthesis converts light to sugar.", None
    if name == "refuser":
        return "I can't help with that.", None
    return None, Exception("boom")


def test_ablation_counts_answer_refuse_error():
    rep = ablate_prompt("Explain photosynthesis.",
                        ["answerer", "refuser", "dead"], _stub, 5)
    by_name = {r["provider"]: r["verdict"] for r in rep["rows"] if
               r["provider"] != "swarm+NSP fallback"}
    assert by_name == {"answerer": "ANSWERED", "refuser": "REFUSED",
                       "dead": "ERROR"}, by_name
    assert (rep["answered"], rep["total"]) == (1, 3)


def test_classifier_both_sides():
    assert classify("The product is 408.", None)[0] == "ANSWERED"
    assert classify("I am unable to help.", None)[0] == "REFUSED"
    assert classify(None, Exception("x"))[0] == "ERROR"
    assert classify("   ", None)[0] == "ERROR"
