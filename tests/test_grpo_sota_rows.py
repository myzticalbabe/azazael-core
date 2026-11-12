# coding: utf-8
"""EP350 / EP351 host edits on GRPOTrainer (see SOTA_ROW_RESCORE.md part D).

Both rows were reassigned onto
`CORE.WORMS.expansion.learning_grpo::GRPOTrainer`, because it is the only local
policy-optimisation implementation in this tree: the rescore's point is that the
offline-replay protocol (EP350) and the weak-to-strong dense reward (EP351) are
trainer-path additions, not new modules beside it.

These tests pin the two properties that make the adoption real rather than
decorative: the offline path updates a policy with **no registry and no
environment**, and both new behaviours are **off unless asked for**.
"""
import os
import sys

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from CORE.WORMS.expansion.learning_grpo import GRPOTrainer  # noqa: E402


def _trainer(**kw):
    # load_policy=False: a test must not read the live policy from the store.
    kw.setdefault("load_policy", False)
    return GRPOTrainer(**kw)


# -- EP350: offline replay protocol ---------------------------------------

def test_offline_replay_updates_the_policy_without_a_registry():
    """A recorded prefix is enough: no WORM_REGISTRY, no environment."""
    t = _trainer()
    out = t.ingest_replay_trajectory({
        "steps": [
            {"context_key": "monitor", "worm": "SystemZ", "reward": 1.0},
            {"context_key": "monitor", "worm": "ProcessZ", "reward": 0.0},
        ]}, source="replay-test")
    assert out["accepted"] is True
    assert out["n_steps"] == 2
    assert out["n_buffered"] == 1

    step = t.replay_offline_step()
    assert step["updated"] is True
    policy = t.get_policy("monitor")
    assert set(policy) == {"SystemZ", "ProcessZ"}
    # Group-normalised advantages moved the winner above the loser.
    assert policy["SystemZ"] > policy["ProcessZ"]


def test_replayed_prefix_splits_by_context_key():
    t = _trainer()
    t.ingest_replay_trajectory({"steps": [
        {"context_key": "a", "worm": "W1", "reward": 1.0},
        {"context_key": "a", "worm": "W2", "reward": 0.0},
        {"context_key": "b", "worm": "W3", "reward": 1.0},
        {"context_key": "b", "worm": "W4", "reward": 0.0},
    ]})
    out = t.replay_offline_step()
    assert set(out["contexts"]) == {"a", "b"}
    assert out["n_steps"] == 4
    assert set(t.get_policy("a")) == {"W1", "W2"}
    assert set(t.get_policy("b")) == {"W3", "W4"}


def test_unusable_trajectory_is_rejected_with_a_reason():
    """An empty ingest must not look like a stored prefix."""
    t = _trainer()
    out = t.ingest_replay_trajectory({"steps": [{"context_key": "x"}]})
    assert out["accepted"] is False
    assert out["reason"] == "no usable steps"
    assert out["n_buffered"] == 0


def test_offline_step_reports_an_empty_buffer():
    out = _trainer().replay_offline_step()
    assert out["updated"] is False
    assert out["reason"] == "no buffered prefix"


def test_replay_buffer_is_bounded():
    t = _trainer()
    for i in range(205):
        t.ingest_replay_trajectory({"steps": [
            {"context_key": "c", "worm": "W%d" % i, "reward": 1.0},
            {"context_key": "c", "worm": "X%d" % i, "reward": 0.0},
        ]})
    assert len(t.replay_batch(limit=1000)) == 200


# -- EP350: step-decaying sampling schedule --------------------------------

def test_step_decay_is_off_by_default_and_decays_when_set():
    off = _trainer()
    assert off.sampling_temperature() == 1.0

    on = _trainer(step_decay=0.5)
    assert on.sampling_temperature() == 1.0           # no updates yet
    on.update_policy(["A", "B"], [1.0, 0.0], "ctx")   # one update
    assert on.sampling_temperature() == 0.5
    on.update_policy(["A", "B"], [1.0, 0.0], "ctx")
    assert on.sampling_temperature() == 0.25


# -- EP351: checkpoint-pair dense implicit reward --------------------------

def test_dense_reward_is_a_no_op_until_weighted():
    t = _trainer()
    rewards = [0.4, 0.1]
    fused = t.apply_dense_reward(["A", "B"], rewards, "ctx")
    assert fused == rewards
    assert fused is not rewards       # a copy, never the caller's own list


def test_update_policy_records_unmodified_rewards_when_dense_is_off():
    t = _trainer()
    t.update_policy(["A", "B"], [1.0, 0.0], "ctx")
    assert t._history[-1]["rewards"] == [1.0, 0.0]


def test_dense_implicit_reward_uses_the_pre_rl_anchor_checkpoint():
    """post-RL policy vs pre-RL anchor, log-ratio over the student's states."""
    t = _trainer(dense_reward_weight=0.25)
    t.update_policy(["A", "B"], [1.0, 0.0], "ctx")
    t.snapshot_anchor("pre-rl")
    t.update_policy(["A", "B"], [1.0, 0.0], "ctx")    # A pulled further ahead

    dense = t.dense_implicit_reward("ctx", anchor_label="pre-rl")
    assert dense["A"] > 0 > dense["B"]

    fused = t.apply_dense_reward(["A", "B"], [0.0, 0.0], "ctx")
    assert fused[0] > 0 > fused[1]


def test_dense_reward_without_a_checkpoint_uses_a_uniform_prior():
    """The first update has no pre-RL side; a uniform prior keeps the term
    computable rather than raising or silently returning nothing."""
    t = _trainer(dense_reward_weight=1.0)
    t.update_policy(["A", "B"], [1.0, 0.0], "ctx")
    dense = t.dense_implicit_reward("ctx")
    assert set(dense) == {"A", "B"}
    assert dense["A"] > 0 > dense["B"]


def test_dense_implicit_reward_returns_nothing_for_an_unknown_context():
    t = _trainer(dense_reward_weight=1.0)
    assert t.dense_implicit_reward("never-seen") == {}
