# coding: utf-8
"""PSYCHE Phase 0 gate: the trait vector scores honestly and round-trips.

- Synthetic respondents at known levels recover those levels
  (all-max-keyed -> ~5.0, all-min -> ~1.0, mid -> ~3.0).
- Keying is balanced 5/5 per domain (acquiescence cannot inflate).
- JSON round-trip is lossless (the editable-artifact property).
- Out-of-range and incomplete responses fail loudly, never silently.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PSYCHE.traits import (DOMAINS, FACETS, ITEMS, TraitVector,
                           domain_scores, keying_balance, score_responses)


def _resp_for(keyed_value: int, unkeyed_value: int):
    return {i: (keyed_value if k else unkeyed_value)
            for i, (_d, _f, k, _t) in enumerate(ITEMS)}


def test_bank_shape():
    assert len(ITEMS) == 60
    assert set(DOMAINS) == {"O", "C", "E", "A", "N", "H"}
    for domain in DOMAINS:
        assert FACETS[domain], domain


def test_keying_balanced_five_five():
    bal = keying_balance()
    for domain in DOMAINS:
        assert bal[domain] == {"+": 5, "-": 5}, (domain, bal[domain])


def test_synthetic_max_recovers_high():
    got = domain_scores(score_responses(_resp_for(5, 1)))
    for domain in DOMAINS:
        assert got[domain] == pytest.approx(5.0), (domain, got[domain])


def test_synthetic_min_recovers_low():
    got = domain_scores(score_responses(_resp_for(1, 5)))
    for domain in DOMAINS:
        assert got[domain] == pytest.approx(1.0), (domain, got[domain])


def test_synthetic_mid_recovers_mid():
    got = domain_scores(score_responses({i: 3 for i in range(len(ITEMS))}))
    for domain in DOMAINS:
        assert got[domain] == pytest.approx(3.0), (domain, got[domain])


def test_json_round_trip_lossless():
    tv = TraitVector.from_responses(_resp_for(4, 2), name="probe")
    assert TraitVector.from_json(tv.to_json()).to_dict() == tv.to_dict()


def test_bad_responses_fail_loudly():
    with pytest.raises(ValueError):
        score_responses({0: 5})
    with pytest.raises(ValueError):
        score_responses({i: (6 if i == 0 else 3) for i in range(len(ITEMS))})
    with pytest.raises(ValueError):
        score_responses([3] * 10)
