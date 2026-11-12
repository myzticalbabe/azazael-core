# coding: utf-8
"""Federated aggregation robustness (audit §4 item 8, TODO P4 item 13).

The aggregator used to be a plain sample-weighted mean, which is the textbook
Byzantine-vulnerable estimator: one client submitting garbage moves the average
by its own weight and *nothing in the result records that it happened*. It also
raised `IndexError` inside the lock when a client sent a vector of a different
length, so one malformed submission broke aggregation for every other client.

Three guards are pinned here, in order of how much they matter:

1. dimensional validation (modal dimension wins; mismatches reported),
2. staleness weighting (an old submission describes an older policy),
3. coordinate-wise trimmed mean (Krum-family robustness).

The tests are written against the *reported* result, not against internals, so
that a future rewrite that stops reporting a guard fails rather than passing
quietly. Deliberately not implemented and therefore not tested: secure
aggregation and differential privacy (no transport for them yet).
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from CORE.WORMS.expansion.federated import FederatedAggregatorWorm


@pytest.fixture(autouse=True)
def clean_fabric():
    """Fabric state is process-wide; start each test from nothing."""
    from CORE import spine_registry as sr

    sr.spine_reset()
    yield
    sr.spine_reset()


def _worm() -> FederatedAggregatorWorm:
    return FederatedAggregatorWorm()


def test_a_dimensional_mismatch_is_reported_instead_of_raising():
    """A short/long client used to raise IndexError *while holding the lock*."""
    w = _worm()
    w.submit("bare", [1.0, 2.0, 3.0, 4.0], 10)
    w.submit("wsl2-ubuntu", [1.0, 2.0, 3.0, 4.0], 10)
    w.submit("broken", [1.0, 2.0], 10)

    out = w.aggregate()
    assert out["success"] is True and out["aggregated"] is True
    assert out["dim"] == 4
    assert out["dropped"] == ["broken"]
    assert out["clients_used"] == 2


def test_an_empty_vector_is_refused_rather_than_averaged():
    w = _worm()
    w.submit("empty", [], 5)
    out = w.aggregate()
    assert out["aggregated"] is False
    assert out["reason"] == "no_usable_weights"


def test_a_corrupted_client_does_not_drag_the_mean():
    """Three honest clients and one absurd one: trimmed mean keeps the truth."""
    w = _worm()
    for env in ("bare", "wsl2-ubuntu", "wsl2-kali"):
        w.submit(env, [1.0, 1.0], 10)
    w.submit("corrupted", [1000.0, 1000.0], 10)

    out = w.aggregate()
    assert out["byzantine"] == "trimmed_mean"
    assert out["trimmed_per_coord"] == 1
    assert out["avg_head"] == [1.0, 1.0], "a Byzantine client moved the average"
    # The naive weighted mean of the same submissions would be 250.75, and the
    # old code had no way to report that it had been.
    assert abs(out["avg_head"][0] - 250.75) > 100


def test_too_few_clients_reports_no_trimming_rather_than_implying_robustness():
    w = _worm()
    w.submit("bare", [1.0], 5)
    w.submit("wsl2-ubuntu", [3.0], 5)
    out = w.aggregate()
    assert out["byzantine"] == "none_too_few_clients"
    assert out["trimmed_per_coord"] == 0
    assert out["avg_head"] == [2.0]


def test_staleness_decay_reduces_an_old_submission():
    """A submission from an hour ago describes an hour-old policy."""
    w = _worm()
    old = _worm()
    old_at = (datetime.now() - timedelta(hours=1)).isoformat()

    w.submit("fresh", [1.0], 1)
    w.submit("stale", [11.0], 1)
    # Backdate the second submission the way a real delayed client would.
    with w._lock:
        w._clients["stale"]["at"] = old_at

    out = w.aggregate()
    assert out["staleness"]["half_life_s"] == w.STALE_HALF_LIFE_S
    assert out["staleness"]["oldest_age_s"] >= 3500
    # Without decay this would be 6.0 (equal weights); with a 600s half-life the
    # hour-old client contributes 1/64 of the weight.
    assert out["avg_head"][0] < 2.0


def test_round_ids_are_monotone_and_visible_after_the_round():
    w = _worm()
    ids = []
    for _ in range(3):
        w.submit("bare", [1.0, 2.0], 1)
        out = w.aggregate()
        ids.append(out["round_id"])
    assert ids == ["r1", "r2", "r3"]

    last = w.execute()["result"]["last_round"]
    assert last["round_id"] == "r3"
    assert last["dim"] == 2
    assert last["clients"] == 1


def test_execute_is_read_only_and_does_not_consume_clients():
    """A Red Queen probe must not clear real client state."""
    w = _worm()
    w.submit("bare", [1.0], 3)
    before = w.execute()["result"]
    assert before["waiting_clients"] == 1
    assert w.execute()["result"]["waiting_clients"] == 1
    assert w._rounds == 0


def test_last_round_survives_a_fabric_round_trip():
    """The round id is part of the operational state the mesh now persists."""
    from CORE import spine_registry as sr

    w = _worm()
    w.submit("bare", [1.0], 1)
    w.aggregate()
    sr.save_spine_state()
    sr.spine_reset()
    sr.load_spine_state()
    restored = sr.get_fabric_state("FederatedAggregatorWorm") or {}
    assert restored.get("_last_round", {}).get("round_id") == "r1"
