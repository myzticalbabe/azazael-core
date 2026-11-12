# coding: utf-8
"""Tests for the Direct-OPD log-ratio reader over stored GRPO snapshots.

snapshot_log_ratios() reads real GRPOTrainer/policy_snapshot rows (never
writes, never tombstones) and scores consecutive pairs by mean log-ratio
over softmaxed per-context score vectors. Identical policies score 0.0.
"""

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.WORMS.expansion.learning_grpo import snapshot_log_ratios


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
    rep = snapshot_log_ratios(store=FakeStore([]))
    assert rep["verdict"] == "no-input"
    assert rep["ok"] is True
    assert rep["pairs"] == []


def test_single_snapshot():
    rep = snapshot_log_ratios(
        store=FakeStore([_snap(9, {"c": {"a": 1.0}})]))
    assert rep["verdict"] == "single-snapshot"
    assert rep["n_snapshots"] == 1


def test_identical_policies_score_zero():
    pol = {"c": {"a": 1.0, "b": 2.0}}
    rep = snapshot_log_ratios(
        store=FakeStore([_snap(2, pol), _snap(1, pol)]))
    assert rep["verdict"] == "pairs"
    assert len(rep["pairs"]) == 1
    assert rep["pairs"][0]["mean_log_ratio"] == 0.0
    assert rep["pairs"][0]["max_abs_log_ratio"] == 0.0


def test_hand_computed_pair():
    # old c: a=1 b=2 -> softmax [0.26894142, 0.73105858];
    # new c: a=2 b=2 -> [0.5, 0.5]; mean log-ratio = 0.12011451.
    # NOTE: an earlier draft of this expectation read 0.120092 from
    # rounded intermediates — exact arithmetic governs, not hand rounding.
    old = {"c": {"a": 1.0, "b": 2.0}}
    new = {"c": {"a": 2.0, "b": 2.0}}
    rep = snapshot_log_ratios(store=FakeStore([_snap(2, new), _snap(1, old)]))
    got = rep["pairs"][0]["mean_log_ratio"]
    assert abs(got - 0.12011451) < 1e-6, got
    assert rep["pairs"][0]["newer_id"] == 2
    assert rep["pairs"][0]["older_id"] == 1
    assert rep["pairs"][0]["n_contexts"] == 1


def test_string_data_parsed_and_nonoverlap_abstains():
    rows = [
        {"id": 2, "source": "GRPOTrainer",
         "event_type": "policy_snapshot",
         "data": json.dumps({"policy": {"c2": {"a": 1.0}}})},
        _snap(1, {"c1": {"a": 1.0}}),
    ]
    rep = snapshot_log_ratios(store=FakeStore(rows))
    assert rep["verdict"] == "no-common-context"
    assert rep["pairs"] == []


def test_garbage_rows_skipped():
    rows = [
        {"id": 3, "source": "GRPOTrainer",
         "event_type": "policy_snapshot", "data": "{broken"},
        {"id": 2, "source": "GRPOTrainer",
         "event_type": "policy_snapshot", "data": {"no-policy": {}}},
        _snap(1, {"c": {"a": 1.0, "b": 1.0}}),
    ]
    rep = snapshot_log_ratios(store=FakeStore(rows))
    assert rep["verdict"] == "single-snapshot"
    assert rep["n_snapshots"] == 1
