# coding: utf-8
"""Tests for the concentration diagnostic over stored GRPO snapshots.

snapshot_concentration() watches the failure mode RIPO's isometry and
ARMOR's anchors both guard — exploration collapse — via per-context
entropy and max share on the NEWER snapshot of each consecutive pair.
A uniform policy scores max entropy; a peaked one scores near-zero
min_entropy with max_max_share near 1.
"""

import math
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.WORMS.expansion.learning_grpo import snapshot_concentration


class FakeStore:
    def __init__(self, rows):
        self._rows = rows

    def query_memories(self, source=None, event_type=None, limit=50, **kw):
        return [r for r in self._rows
                if (source is None or r.get("source") == source)
                and (event_type is None or r.get("event_type") == event_type)
                ][:limit]


def _snap(i, policy):
    return {"id": i, "source": "GRPOTrainer",
            "event_type": "policy_snapshot", "data": {"policy": policy}}


def test_no_input():
    rep = snapshot_concentration(store=FakeStore([]))
    assert rep["verdict"] == "no-input"
    assert rep["ok"] is True
    assert rep["pairs"] == []


def test_single_snapshot():
    rep = snapshot_concentration(
        store=FakeStore([_snap(9, {"c": {"a": 1.0}})]))
    assert rep["verdict"] == "single-snapshot"
    assert rep["n_snapshots"] == 1


def test_uniform_policy_max_entropy():
    pol = {"c": {"a": 0.0, "b": 0.0}}
    rep = snapshot_concentration(
        store=FakeStore([_snap(2, pol), _snap(1, pol)]))
    assert rep["verdict"] == "pairs"
    pair = rep["pairs"][0]
    assert abs(pair["mean_entropy"] - math.log(2)) < 1e-6, pair
    assert pair["max_max_share"] == 0.5
    assert pair["n_contexts"] == 1


def test_peaked_policy_collapsed():
    old = {"c": {"a": 0.0, "b": 0.0}}
    new = {"c": {"a": 10.0, "b": 0.0}}
    rep = snapshot_concentration(
        store=FakeStore([_snap(2, new), _snap(1, old)]))
    assert rep["verdict"] == "pairs"
    pair = rep["pairs"][0]
    assert pair["min_entropy"] < 0.01, pair
    assert pair["max_max_share"] > 0.99, pair


def test_nonoverlap_abstains():
    rows = [_snap(2, {"c2": {"a": 1.0}}), _snap(1, {"c1": {"a": 1.0}})]
    rep = snapshot_concentration(store=FakeStore(rows))
    assert rep["verdict"] == "no-common-context"
    assert rep["pairs"] == []
