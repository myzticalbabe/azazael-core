# coding: utf-8
"""Replay path: deterministic under seed, shaped like legacy results."""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from CORE.WORMS.expansion.learning_rl import RLTrainLoop

RECS = [{"source": "t%d" % (i % 5), "severity": s, "data": {}}
        for i, s in enumerate(["info", "warning", "error"] * 20)]


def _run(**kw):
    rl = RLTrainLoop()
    return rl.train_from_memory(records=list(RECS), n_episodes=5,
                                max_steps=10, persist=False,
                                rng=random.Random(9), **kw)


def test_replay_deterministic_same_seed():
    a = _run(use_replay=True)["avg_reward"]
    b = _run(use_replay=True)["avg_reward"]
    assert a == b


def test_replay_result_shape_matches_legacy():
    out = _run(use_replay=True)
    assert out["episodes"] == 5
    assert isinstance(out["avg_reward"], float)
    assert out["q_table_size"] > 0


def test_replay_off_by_default():
    import inspect
    sig = inspect.signature(RLTrainLoop.train_from_memory)
    assert sig.parameters["use_replay"].default is False
