# coding: utf-8
"""EP155 MGPO: multi-granularity group-relative advantages on GRPOTrainer.

Whole-group normalisation washes out sub-group signal; MGPO blends a
coarse (whole-group) and a fine (sub-group) normalisation. These tests
are hermetic: load_policy=False, no store, no registry, no environment.
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from CORE.WORMS.expansion.learning_grpo import GRPOTrainer  # noqa: E402


def _trainer(**kw):
    kw.setdefault("load_policy", False)
    return GRPOTrainer(**kw)


def test_no_subgroups_matches_coarse():
    """Without a partition MGPO reduces to plain group-norm."""
    t = _trainer()
    rewards = [1.0, 0.5, 0.0, -0.5]
    assert t.compute_mgpo_advantages(rewards) == t.compute_advantages(rewards)


def test_subgroup_signal_survives_coarse_washout():
    """Best of a weak sub-group keeps positive advantage at fine weight."""
    t = _trainer()
    # Group A strong overall, group B weak; B0 is B's best but below mean.
    rewards = [1.0, 0.9, 0.0, -0.1]
    coarse = t.compute_advantages(rewards)
    assert coarse[2] < 0  # washed out at whole-group granularity
    blended = t.compute_mgpo_advantages(rewards, subgroups=[[0, 1], [2, 3]],
                                        coarse_weight=0.0)
    assert blended[2] > 0  # fine level sees B0 beats B1
    assert blended[0] > blended[1]  # A order preserved within sub-group


def test_blend_weight_interpolates():
    """coarse_weight=1.0 is coarse; 0.5 sits strictly between."""
    t = _trainer()
    rewards = [1.0, 0.9, 0.0, -0.1]
    sub = [[0, 1], [2, 3]]
    coarse = t.compute_mgpo_advantages(rewards, subgroups=sub, coarse_weight=1.0)
    fine = t.compute_mgpo_advantages(rewards, subgroups=sub, coarse_weight=0.0)
    mid = t.compute_mgpo_advantages(rewards, subgroups=sub, coarse_weight=0.5)
    assert coarse == t.compute_advantages(rewards)
    for c, f, m in zip(coarse, fine, mid):
        assert m == (c + f) / 2


def test_uncovered_members_keep_coarse():
    """Members in no sub-group fall back to the coarse value."""
    t = _trainer()
    rewards = [1.0, 0.5, 0.0]
    out = t.compute_mgpo_advantages(rewards, subgroups=[[0, 1]],
                                    coarse_weight=0.0)
    coarse = t.compute_advantages(rewards)
    assert out[2] == coarse[2]


def test_empty_rewards_empty_out():
    """Empty in, empty out — no division, no crash."""
    assert _trainer().compute_mgpo_advantages([]) == []
