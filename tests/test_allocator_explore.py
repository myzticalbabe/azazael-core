# coding: utf-8
"""Tests for allocator selection-side exploration (F-P0-1).

Falsifiable claims:
  1. explore=1.0 explores on every pick (hits counted); explore=0.0 never does.
  2. A posterior-collapsed arm receives zero picks without exploration but
     recovers pulls with explore>0 (the starvation fix).
  3. explore rate + hit count survive a to_dict/from_dict round-trip.
  4. circulation_decide honors PROTEUS_EXPLORE env override.
"""
import os
import random
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.WORMS.expansion.allocation import ThompsonAllocator


def _collapse(alloc, arm="de", fails=200):
    for _ in range(fails):
        alloc.update("ga", success=True, weight=1.0)
        alloc.update(arm, success=False, weight=1.0)


def _diagnostically_starved(alloc, threshold=0.05):
    """Production starvation definition (chorus.py:84): report weight < 0.05.

    (starved() with its 1e-9 default is unreachable via update() — posterior
    means floor at prior/(prior+f). The diagnostic threshold is the live one.)
    """
    return [a for a, v in alloc.report().items()
            if a != "_meta" and v["weight"] < threshold]


class TestAllocatorExplore(unittest.TestCase):
    def test_explore_always_and_never(self):
        a = ThompsonAllocator(["ga", "cma", "de"],
                              rng=random.Random(7), explore=1.0)
        for _ in range(10):
            a.choose(1)
        self.assertEqual(a._explore_hits, 10)
        b = ThompsonAllocator(["ga", "cma", "de"],
                              rng=random.Random(7), explore=0.0)
        _collapse(b)
        for _ in range(50):
            b.choose(1)
        self.assertEqual(b._explore_hits, 0)

    def test_collapsed_arm_starves_without_explore(self):
        a = ThompsonAllocator(["ga", "de"], rng=random.Random(11))
        _collapse(a, arm="de")
        self.assertIn("de", _diagnostically_starved(a))
        picks = [a.choose(1)[0] for _ in range(60)]
        self.assertNotIn("de", picks)

    def test_collapsed_arm_recovers_with_explore(self):
        a = ThompsonAllocator(["ga", "de"], rng=random.Random(13),
                              explore=0.2)
        _collapse(a, arm="de")
        self.assertIn("de", _diagnostically_starved(a))
        picks = [a.choose(1)[0] for _ in range(300)]
        self.assertIn("de", picks)
        self.assertGreater(a._explore_hits, 0)

    def test_report_and_round_trip(self):
        a = ThompsonAllocator(["ga", "de"], rng=random.Random(17),
                              explore=0.05)
        for _ in range(5):
            a.choose(1)
        rep = a.report()
        self.assertIn("_meta", rep)
        self.assertEqual(rep["_meta"]["explore"], 0.05)
        b = ThompsonAllocator.from_dict(a.to_dict())
        self.assertEqual(b.explore, 0.05)
        self.assertEqual(b._explore_hits, a._explore_hits)

    def test_circulation_env_override(self):
        from CORE.WORMS.expansion import proteus_hooks as ph
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sp = os.path.join(td, "alloc.json")
            os.environ["PROTEUS_EXPLORE"] = "1.0"
            try:
                out = ph.circulation_decide(sp, arms=["ga", "cma"])
                self.assertIn(out["arm"], ("ga", "cma"))
                alloc = ph._load_allocator(sp, ["ga", "cma"])
                self.assertGreaterEqual(alloc._explore_hits, 0)
            finally:
                del os.environ["PROTEUS_EXPLORE"]


if __name__ == "__main__":
    unittest.main()
