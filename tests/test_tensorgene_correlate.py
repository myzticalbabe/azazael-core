# coding: utf-8
"""Tests for tensorgene.correlate (gene relevance ranking).

2026-09-28: replaces test_boruta_relevance.py. The boruta all-relevant
ranker was superseded by correlation-based ranking in tensorgene
(correlate/spearman); nothing imports boruta_relevance anymore. These
tests pin the successor contract instead of the removed algorithm.
"""
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.WORMS.expansion.tensorgene import correlate


def _row(genes, fitness):
    return {"data": {"best_genes": genes, "best_total": fitness}}


class TestCorrelateRelevance(unittest.TestCase):
    def test_confirms_driver_rejects_noise(self):
        import random
        rng = random.Random(11)
        rows = []
        for i in range(60):
            x = rng.uniform(0, 1)
            noise = rng.uniform(0, 1)
            rows.append(_row({"driver": x, "noise": noise}, x * 10.0))
        d = correlate(rows, "driver")
        n = correlate(rows, "noise")
        self.assertTrue(d["identifiable"])
        self.assertGreater(d["usefulness"], 0.8)
        self.assertEqual(d["direction"], "up")
        self.assertLess(n["usefulness"], 0.4)

    def test_too_few_rows_not_identifiable(self):
        rows = [_row({"a": 1.0}, 2.0) for _ in range(5)]
        out = correlate(rows, "a")
        self.assertFalse(out["identifiable"])
        self.assertEqual(out["usefulness"], 0.0)

    def test_missing_gene_skipped(self):
        rows = [_row({"a": float(i)}, float(i)) for i in range(30)]
        out = correlate(rows, "ghost")
        self.assertFalse(out["identifiable"])

    def test_never_raises(self):
        for bad in (None, [], [{"junk": 1}], "nope"):
            out = correlate(bad, "a")
            self.assertFalse(out["identifiable"])


if __name__ == "__main__":
    unittest.main()
