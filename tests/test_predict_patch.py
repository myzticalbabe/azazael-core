# coding: utf-8
"""Tests for the proposal-outcome ML loop (refuses honestly when thin)."""
import importlib.util
import json
import sys
import unittest
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import sklearn
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


def _load():
    spec = importlib.util.spec_from_file_location(
        "predict_patch_success_test", ROOT / "scripts" / "predict_patch_success.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row(retained, **kw):
    r = {"severity": "HIGH", "pattern_sites": 0, "n_attempts": 1,
         "agreement": "", "models": ["eve-v2u"], "retained": retained}
    r.update(kw)
    return r


class TestPredictor(unittest.TestCase):
    def test_featurize_shape(self):
        mod = _load()
        self.assertEqual(len(mod.featurize(_row(True))), 5)

    @pytest.mark.skipif(not HAS_SKLEARN, reason="sklearn not installed")
    def test_refuses_single_class(self):
        mod = _load()
        rows = [_row(True) for _ in range(40)]
        rep = mod.train(rows)
        self.assertFalse(rep["ok"])

    @pytest.mark.skipif(not HAS_SKLEARN, reason="sklearn not installed")
    def test_learns_separable_signal(self):
        mod = _load()
        import random
        rng = random.Random(3)
        rows = []
        for _ in range(30):
            u = rng.random() < 0.5
            rows.append(_row(u, agreement="unanimous" if u else "split"))
        rep = mod.train(rows)
        self.assertTrue(rep["ok"], rep.get("reason"))
        self.assertGreater(rep["cv_accuracy"], 0.8)

    def test_cli_refuses_thin_data(self):
        import tempfile
        mod = _load()
        with tempfile.TemporaryDirectory() as td:
            mod.OUTCOMES = Path(td) / "empty.jsonl"
            self.assertEqual(mod.main([]), 3)


if __name__ == "__main__":
    unittest.main()
