# coding: utf-8
"""Tests for src/CORE/reward_scales.py — canonical reward co-tabulation."""
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from CORE.reward_scales import (  # noqa: E402
    SCALES,
    canonical_reward,
    describe_scale,
    emit_canonical,
    scale_distributions,
    to_common_scale,
)
from CORE.local_memory import LocalMemoryStore  # noqa: E402


def _tmp_store():
    tmp = tempfile.mkdtemp(prefix="rewardscales_")
    return LocalMemoryStore(db_path=os.path.join(tmp, "t.db"))


def test_scales_registered():
    for sid in ("rate_01", "boolean", "count", "millis", "fitness_points",
                "zscore", "loss", "margin", "probability", "severity_payoff"):
        assert sid in SCALES
        assert "higher_better" in SCALES[sid]
    assert describe_scale("nope") == {}


def test_canonical_valid_and_shaped():
    rec = canonical_reward(0.75, "rate_01", "probe")
    assert rec == {"value": 0.75, "scale_id": "rate_01",
                   "provenance": "probe", "ok": True, "reason": ""}


def test_canonical_rejects_honestly():
    assert canonical_reward(0.5, "nope", "p")["reason"] == "unknown-scale"
    bad = canonical_reward("x", "rate_01", "p")
    assert bad["ok"] is False and bad["reason"] == "non-numeric"
    nan = canonical_reward(float("nan"), "rate_01", "p")
    assert nan["ok"] is False and nan["value"] is None
    over = canonical_reward(2.0, "rate_01", "p")
    assert over["ok"] is True and "out-of-range" in over["reason"]
    b = canonical_reward(True, "boolean", "p")
    assert b["ok"] is True and b["value"] == 1
    assert canonical_reward(True, "rate_01", "p")["ok"] is False


def test_to_common_scale_math_and_refusals():
    base = {"mean": 10.0, "std": 2.0}
    assert to_common_scale({"value": 14.0}, base) == pytest.approx(2.0)
    assert to_common_scale({"value": 14.0}, None) is None
    assert to_common_scale({"value": 14.0}, {}) is None
    assert to_common_scale({"value": 14.0}, {"mean": 10.0, "std": 0.0}) is None
    assert to_common_scale({"value": "x"}, base) is None
    assert to_common_scale({}, base) is None


def test_emit_roundtrip_and_distributions():
    store = _tmp_store()
    assert emit_canonical(store, 0.8, "rate_01", "probe", worm="w1") is True
    assert emit_canonical(store, 120.0, "millis", "probe", worm="w1") is True
    rows = store.query_memories(source="reward-ledger", limit=10)
    assert len(rows) == 2
    dist = scale_distributions(store)
    assert dist["rate_01"]["n"] == 1
    assert dist["millis"]["mean"] == pytest.approx(120.0)
    assert dist["millis"]["std"] == pytest.approx(0.0)
    # end-to-end: measured baseline feeds the mapping
    z = to_common_scale({"value": 0.8}, {"mean": 0.8, "std": 0.0} | {})
    assert z is None  # zero variance refuses, even on real data
