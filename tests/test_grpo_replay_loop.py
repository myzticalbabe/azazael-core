#!/usr/bin/env python3
"""GRPO replay integration: live updates feed the offline buffer in place.

update_policy self-ingests each group as a replay trajectory, so
replay_offline_step always has material from real runs; the offline
path passes record_replay=False so buffered steps are not re-buffered.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from CORE.WORMS.expansion.learning_grpo import GRPOTrainer


def _trainer(**kw):
    args = {"load_policy": False}
    args.update(kw)
    return GRPOTrainer(**args)


def test_live_update_buffers_replay_trajectory():
    trainer = _trainer()
    assert trainer.replay_batch() == []
    trainer.update_policy(["a", "b", "c"], [0.2, 0.9, 0.4],
                          context_key="ctx")
    buffered = trainer.replay_batch()
    assert len(buffered) == 1
    assert buffered[0]["source"] == "update_policy:ctx"
    assert [s["worm"] for s in buffered[0]["steps"]] == ["a", "b", "c"]


def test_offline_step_consumes_buffer_without_rebuffering():
    trainer = _trainer()
    trainer.update_policy(["a", "b"], [0.3, 0.8], context_key="ctx")
    before = len(trainer.replay_batch(200))
    out = trainer.replay_offline_step()
    assert out["updated"] is True
    assert "ctx" in out["contexts"]  # update_policy returns None on success
    assert len(trainer.replay_batch(200)) == before


def test_offline_step_empty_buffer_reports_not_updated():
    trainer = _trainer()
    out = trainer.replay_offline_step()
    assert out == {"updated": False, "reason": "no buffered prefix"}


def test_buffer_stays_capped():
    trainer = _trainer()
    for i in range(5):
        trainer.update_policy(["a", "b"], [0.1 * i, 0.2 * i + 0.1],
                              context_key="c%d" % i)
    assert len(trainer.replay_batch(200)) == 5
