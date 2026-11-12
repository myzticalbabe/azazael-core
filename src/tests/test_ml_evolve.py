# coding: utf-8
"""
Unit tests for ML.nsga_tuner - NSGA-II tuning of agent hyperparameters.
Uses a mock agent harness: no models, no network.
"""

import sys
import unittest
from pathlib import Path

# Ensure azazael/src is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from ML.nsga_tuner import AgentGenome, decode, make_objectives, tune
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def _mock_run(genome, task):
    # Succeeds iff temperature is low (deterministic stand-in).
    ok = genome.temperature <= 0.35
    return ok, 1.0 + genome.max_iterations * 0.1


_TASKS = [
    {"prompt": "p1", "expect": "a"},
    {"prompt": "p2", "expect": "b"},
]


class TestDecode(unittest.TestCase):
    def test_ranges(self):
        g = decode(np.array([0.0, 0.0]))
        self.assertEqual(g, AgentGenome(temperature=0.0, max_iterations=1))
        g = decode(np.array([1.0, 1.0]))
        self.assertEqual(g, AgentGenome(temperature=0.7, max_iterations=8))

    def test_clips(self):
        g = decode(np.array([-5.0, 99.0]))
        self.assertEqual(g, AgentGenome(temperature=0.0, max_iterations=8))


class TestObjectives(unittest.TestCase):
    def test_error_and_time(self):
        objs = make_objectives(_TASKS, _mock_run)
        cool = np.array([0.0, 0.0])  # temp 0 -> success
        hot = np.array([1.0, 1.0])  # temp 0.7 -> fail
        self.assertEqual(objs[0](cool), 0.0)
        self.assertEqual(objs[0](hot), 1.0)
        # More iterations cost more time budget.
        self.assertLess(objs[1](cool), objs[1](hot))


class TestTune(unittest.TestCase):
    def test_pareto_nonempty(self):
        genomes, front = tune(_TASKS, _mock_run, pop_size=8, generations=2, seed=0)
        self.assertGreater(len(genomes), 0)
        self.assertEqual(front.shape[1], 2)
        for genome in genomes:
            self.assertIsInstance(genome, AgentGenome)
            self.assertGreaterEqual(genome.temperature, 0.0)
            self.assertLessEqual(genome.temperature, 0.7)
            self.assertGreaterEqual(genome.max_iterations, 1)
            self.assertLessEqual(genome.max_iterations, 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
