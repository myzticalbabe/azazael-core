# coding: utf-8
"""
Unit tests for ML.nsga - NSGA-II multi-objective optimizer.
"""

import sys
import unittest
from pathlib import Path

# Ensure azazael/src is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from ML.nsga import (
    NSGA2,
    crowding_distance,
    dominates,
    non_dominated_sort,
)
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def _zdt1(x):
    # ZDT1, 10-D: f1 = x0, g = 1 + 9*mean(x[1:]), f2 = g*(1 - sqrt(f1/g)).
    # True front: f2 = 1 - sqrt(f1), f1 in [0, 1].
    f1 = x[0]
    g = 1.0 + 9.0 * np.mean(x[1:])
    return f1, g * (1.0 - np.sqrt(f1 / g))


class TestDominance(unittest.TestCase):
    def test_dominates(self):
        self.assertTrue(dominates(np.array([1.0, 1.0]), np.array([2.0, 2.0])))
        self.assertFalse(dominates(np.array([2.0, 2.0]), np.array([1.0, 1.0])))
        self.assertFalse(dominates(np.array([1.0, 2.0]), np.array([2.0, 1.0])))
        self.assertFalse(dominates(np.array([1.0, 1.0]), np.array([1.0, 1.0])))

    def test_sort_two_fronts(self):
        # [0] dominates both; [2]=[0,1] dominates [1]=[1,1]: three fronts.
        objs = np.array([[0.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
        fronts = non_dominated_sort(objs)
        self.assertEqual(fronts[0], [0])
        self.assertEqual(fronts[1], [2])
        self.assertEqual(fronts[2], [1])

    def test_crowding_endpoints_infinite(self):
        objs = np.array([[0.0, 1.0], [0.5, 0.5], [1.0, 0.0]])
        dist = crowding_distance(objs)
        self.assertTrue(np.isinf(dist[0]) and np.isinf(dist[2]))
        self.assertGreater(dist[1], 0.0)


class TestZDT1(unittest.TestCase):
    def test_front_shape(self):
        opt = NSGA2(
            [lambda x: _zdt1(x)[0], lambda x: _zdt1(x)[1]],
            bounds=np.tile([0.0, 1.0], (10, 1)),
            pop_size=50,
            seed=0,
        )
        _, front = opt.run(150)
        self.assertGreaterEqual(len(front), 10)
        # Front hugs f2 = 1 - sqrt(f1) within tolerance.
        err = np.abs(front[:, 1] - (1.0 - np.sqrt(np.clip(front[:, 0], 0, 1))))
        self.assertLess(np.mean(err), 0.05)
        # Endpoints span the range.
        self.assertLess(front[:, 0].min(), 0.2)
        self.assertGreater(front[:, 0].max(), 0.7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
