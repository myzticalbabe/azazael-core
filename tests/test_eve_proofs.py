"""Funnels prove themselves or they are narrative.

The Root's own verdict is sealed here: artifact digests bound to the
audit rule, verified against the live manifest. Tamper with one blob
and the proof names it.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from eve_unleashed.proofs import funnel_proof, verify_funnel


def test_empty_funnel_proves_nothing():
    with pytest.raises(ValueError, match="no inputs"):
        funnel_proof({}, "majority")
    with pytest.raises(ValueError, match="no stated rule"):
        funnel_proof({"a": "abc"}, "")


def test_seal_and_replay_pass():
    p = funnel_proof({"genome": "aa11", "null": "bb22"}, "same ruler, tie kept")
    v = verify_funnel(p, {"genome": "aa11", "null": "bb22", "extra": "cc33"})
    assert v["verdict"] == "PASS"
    assert v["missing"] == [] and v["mismatched"] == []


def test_tamper_is_named_not_hidden():
    p = funnel_proof({"genome": "aa11", "null": "bb22"}, "same ruler")
    v = verify_funnel(p, {"genome": "aa11", "null": "CHANGED"})
    assert v["verdict"] == "FAIL"
    assert v["mismatched"] == ["null"]
    v2 = verify_funnel(p, {"genome": "aa11"})
    assert v2["verdict"] == "FAIL"
    assert v2["missing"] == ["null"]


def test_root_verdict_is_sealed_live():
    man = json.load(open("state/honey/honey.json", encoding="utf-8"))
    arts = man["artifacts"]
    p = funnel_proof(dict(arts), "all cited artifacts verify against blobs")
    assert verify_funnel(p, arts)["verdict"] == "PASS"
