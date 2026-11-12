# coding: utf-8
"""Tests for boruta_relevance (all-relevant gene ranking)."""
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.WORMS.expansion.tensorgene import boruta_relevance


def _row(genes, fitness):
    return {"data": {"best_genes": genes, "best_total": fitness}}


# Check if boruta/sklearn available
try:
    import sklearn
    import boruta
    HAS_BORUTA = True
except ImportError:
    HAS_BORUTA = False


class TestBorutaRelevance(unittest.TestCase):
    @unittest.skipIf(not HAS_BORUTA, "sklearn/boruta not installed")
    def test_confirms_driver_rejects_noise(self):
        import random
        rng = random.Random(11)
        rows = []
        for i in range(60):
            x = rng.uniform(0, 1)
            noise = rng.uniform(0, 1)
            rows.append(_row({"driver": x, "rider": x * 0.2 + rng.uniform(0, 0.01),
                              "noise": noise}, x * 10.0))
        out = boruta_relevance(rows, max_iter=15)
        self.assertTrue(out["ok"], out.get("reason"))
        self.assertEqual(out["genes"]["driver"], "confirmed")
        self.assertEqual(out["genes"]["noise"], "rejected")

    def test_too_few_rows_fail_open(self):
        rows = [_row({"a": 1.0}, 2.0) for _ in range(5)]
        out = boruta_relevance(rows)
        self.assertFalse(out["ok"])
        self.assertIn("usable rows", out["reason"])

    def test_missing_genes_skipped(self):
        rows = [_row({"a": float(i)}, float(i)) for i in range(30)]
        out = boruta_relevance(rows, max_iter=10)
        # Single gene with majority coverage: still needs >= 2 genes.
        self.assertFalse(out["ok"])

    def test_never_raises(self):
        out = boruta_relevance(None)
        self.assertFalse(out["ok"])
        out = boruta_relevance([{"junk": 1}])
        self.assertFalse(out["ok"])
