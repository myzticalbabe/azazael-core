"""G1 test for src/eve_unleashed/debunk.py.

Myth-debunking is pure text analysis (no network): verdicts must be
deterministic per input, self-contradiction must refute, opinion-only
input must be unverifiable, and fallacy shapes must flag.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed.debunk import (debunk_claim, extract_propositions,
                                  flag_fallacies)


def test_self_contradiction_refutes():
    res = debunk_claim("This always works except when it does not work.")
    assert res["verdict"] == "REFUTED"
    assert res["contradictions"]


def test_opinion_only_is_unverifiable():
    res = debunk_claim("In my opinion this is disgusting and everyone knows it.")
    assert res["verdict"] == "UNVERIFIABLE"
    assert res["opinions"]


def test_weasel_flags_suspect():
    res = debunk_claim("Studies show the river flows north through Springfield.")
    assert res["verdict"] == "SUSPECT"
    assert [f["flag"] for f in res["fallacy_flags"]] == ["weasel_authority"]


def test_plain_claim_is_checkable():
    res = debunk_claim("The river flows north through Springfield.")
    assert res["verdict"] == "CHECKABLE"
    assert res["propositions"] == ["The river flows north through Springfield."]
    assert "eve factcheck" in res["next"]


def test_empty_claim_rejected():
    with pytest.raises(ValueError):
        debunk_claim("   ")


def test_proposition_opinion_split():
    props, opinions = extract_propositions(
        "The dam was built in 1962. In my opinion it should be removed.")
    assert len(props) == 1 and len(opinions) == 1
    assert opinions[0]["cues"]


def test_ad_hominem_and_anecdote_flagged():
    flags = dict((f, m) for f, m in flag_fallacies(
        "My neighbor says the mayor is a fraud and a liar."))
    assert "ad_hominem" in flags
    assert "anecdote_as_data" in flags
