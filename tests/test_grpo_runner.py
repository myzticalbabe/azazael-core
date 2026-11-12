# coding: utf-8
"""Unit tests for the DPO preference machinery (ABTestRunner).

Covers the B6 gap on learning_grpo.py. Pure in-memory tests — no store.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import math
import pytest

from CORE.WORMS.expansion.learning_grpo import ABTestRunner


@pytest.fixture
def runner():
    return ABTestRunner()


class TestRecordAndGet:
    def test_empty_experiment(self, runner):
        assert runner.get_preference_pairs("nope") == []
        r = runner.compute_dpo_loss("nope")
        assert r["loss"] is None and r["n_pairs"] == 0

    def test_record_roundtrip(self, runner):
        e = runner.record_preference(
            "exp1", chosen="a", rejected="b",
            context={"task_type": "scheduled"}, chosen_reward=1.0, rejected_reward=0.0)
        assert e["reward_margin"] == 1.0
        pairs = runner.get_preference_pairs("exp1")
        assert len(pairs) == 1
        assert pairs[0]["chosen"] == "a" and pairs[0]["rejected"] == "b"

    def test_experiments_isolated(self, runner):
        runner.record_preference("e1", chosen="a", rejected="b",
                                 chosen_reward=1.0, rejected_reward=0.0)
        assert runner.get_preference_pairs("e2") == []


class TestDpoLoss:
    def test_loss_math(self, runner):
        runner.record_preference("exp", chosen="a", rejected="b",
                                 chosen_reward=1.0, rejected_reward=0.0)
        r = runner.compute_dpo_loss("exp", beta=0.1)
        expect = math.log(1.0 + math.exp(-0.1))
        assert r["n_pairs"] == 1
        assert abs(r["loss"] - round(expect, 6)) < 1e-9
        assert r["avg_margin"] == 1.0

    def test_bigger_margin_smaller_loss(self, runner):
        runner.record_preference("e1", chosen="a", rejected="b",
                                 chosen_reward=1.0, rejected_reward=0.0)
        runner.record_preference("e2", chosen="a", rejected="b",
                                 chosen_reward=10.0, rejected_reward=0.0)
        assert (runner.compute_dpo_loss("e2")["loss"] <
                runner.compute_dpo_loss("e1")["loss"])

    def test_preference_scores(self, runner):
        runner.record_preference("exp", chosen="a", rejected="b",
                                 chosen_reward=1.0, rejected_reward=0.0)
        runner.record_preference("exp", chosen="a", rejected="c",
                                 chosen_reward=1.0, rejected_reward=0.0)
        s = runner.compute_preference_scores("exp")
        assert s["a"]["preference_score"] == 1.0
        assert s["b"]["preference_score"] == 0.0
